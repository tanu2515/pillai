import { useCallback, useEffect, useState } from "react";
import { View, Text, ScrollView, Pressable, TextInput, StyleSheet } from "react-native";
import { useFocusEffect, router } from "expo-router";
import * as Location from "expo-location";
import { api, apiPost, getEmail, setEmail as saveEmail } from "../src/api";
import { colors, radius, spacing, shadow, levelColor } from "../src/theme";
import { ChatFab } from "../src/components/ChatFab";

type Gate = { id: number; name: string; remaining: number; capacity_pressure_pct: number; level: string };
type Advisory = { gates: Gate[]; suggestion: { crowded_gate: string; crowded_pct: number; suggested_gate: string; suggested_pct: number } | null };
type OffPeak = { current_level: string; recommendation: string };
type MyEvent = { event_attendee_id: number; event_name: string; is_current: boolean; is_live: boolean; registration_status: string };
type EvacRoute = { id: number; name: string; distance_km: number | null; is_accessible: boolean; recommended: boolean };
type Evacuation = {
  emergency_active: boolean; emergency_zone: string | null; all_exits_congested: boolean;
  from_attendee_location: boolean; routes: EvacRoute[];
};

export default function LiveStatus() {
  const [event, setEvent] = useState<string>("Loading event…");
  const [advisory, setAdvisory] = useState<Advisory>({ gates: [], suggestion: null });
  const [offPeak, setOffPeak] = useState<OffPeak[]>([]);
  const [aiText, setAiText] = useState<string | null>(null);
  const [email, setEmailState] = useState("");
  const [emailInput, setEmailInput] = useState("");
  const [myEvents, setMyEvents] = useState<MyEvent[]>([]);
  const [accessibleOnly, setAccessibleOnly] = useState(false);
  const [evac, setEvac] = useState<Evacuation>({
    emergency_active: false, emergency_zone: null, all_exits_congested: false, from_attendee_location: false, routes: [],
  });
  const [location, setLocation] = useState<{ lat: number; lng: number } | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const { status } = await Location.requestForegroundPermissionsAsync();
        if (status !== "granted") return;
        const pos = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced });
        setLocation({ lat: pos.coords.latitude, lng: pos.coords.longitude });
      } catch {
        // No GPS available (simulator, permission denied, etc.) — evacuation
        // routing just falls back to distance-from-venue instead of from me.
      }
    })();
  }, []);

  const load = useCallback(async () => {
    const locationQuery = location ? `&lat=${location.lat}&lng=${location.lng}` : "";
    const [ev, adv, ai, offpeak, evacData] = await Promise.all([
      api<{ configured: boolean; name?: string }>("/api/event"),
      api<Advisory>("/api/advisory"),
      api<{ text: string | null; grounded_in: string[] }>("/api/ai/attendee-advisory"),
      api<OffPeak[]>("/api/offpeak"),
      api<Evacuation>(`/api/evacuation-routes?accessible_only=${accessibleOnly}${locationQuery}`),
    ]);
    setEvent(ev.configured ? ev.name || "" : "No event configured yet");
    setAdvisory(adv);
    setAiText(ai.text);
    setOffPeak(offpeak);
    setEvac(evacData);

    const storedEmail = await getEmail();
    setEmailState(storedEmail);
    if (storedEmail) {
      const events = await api<MyEvent[]>(`/api/attendee/my-events?email=${encodeURIComponent(storedEmail)}`);
      setMyEvents(events);
    }
  }, [accessibleOnly, location]);

  useFocusEffect(
    useCallback(() => {
      load();
      const interval = setInterval(load, 5000);
      return () => clearInterval(interval);
    }, [load])
  );

  async function continueWithEmail() {
    if (!emailInput.trim()) return;
    await saveEmail(emailInput.trim());
    await load();
  }

  async function registerForCurrent() {
    const em = await getEmail();
    if (!em) return;
    await apiPost("/api/attendee/register-event", { email: em });
    await load();
  }

  async function switchEvent(id: number) {
    const em = await getEmail();
    await apiPost("/api/attendee/current-event", { email: em, event_attendee_id: id });
    await load();
  }

  async function toggleAccessible() {
    const next = !accessibleOnly;
    setAccessibleOnly(next);
    if (next) {
      const em = await getEmail();
      await apiPost("/api/attendee/accessibility-request", {
        email: em || null,
        ...(location ? { lat: location.lat, lng: location.lng } : {}),
      });
    }
  }

  const worst = advisory.gates.reduce<Gate | null>((a, b) => (!a || b.capacity_pressure_pct > a.capacity_pressure_pct ? b : a), null);

  return (
    <View style={{ flex: 1, backgroundColor: colors.bg }}>
      <ScrollView contentContainerStyle={{ padding: spacing.lg, paddingBottom: 60 }}>
        <Pressable onPress={() => router.back()}>
          <Text style={styles.backText}>← Back</Text>
        </Pressable>
        <Text style={styles.title}>Live Event Status</Text>
        <Text style={styles.subtitle}>{event}</Text>

        {advisory.suggestion && (
          <View style={styles.alertBanner}>
            <Text style={styles.alertIcon}>⚠️</Text>
            <View style={{ flex: 1 }}>
              <Text style={styles.alertTitle}>LIVE ALERT</Text>
              <Text style={styles.alertText}>
                {advisory.suggestion.crowded_gate} is currently crowded ({advisory.suggestion.crowded_pct}%). Please use{" "}
                {advisory.suggestion.suggested_gate} for faster entry ({advisory.suggestion.suggested_pct}%).
              </Text>
            </View>
          </View>
        )}

        <View style={styles.statRow}>
          <View style={styles.statTile}>
            <Text style={styles.statLabel}>Crowd</Text>
            <Text style={[styles.statValue, { color: worst ? levelColor[worst.level] : colors.ink }]}>{worst?.level || "—"}</Text>
          </View>
        </View>

        <View style={[styles.card, evac.emergency_active && styles.evacCardEmergency]}>
          <Text style={styles.cardTitle}>♿ Accessibility &amp; Evacuation</Text>
          <Pressable style={styles.accessToggle} onPress={toggleAccessible}>
            <View style={[styles.checkbox, accessibleOnly && styles.checkboxOn]}>
              {accessibleOnly && <Text style={styles.checkboxMark}>✓</Text>}
            </View>
            <Text style={styles.mutedText}>I need a wheelchair-accessible exit</Text>
          </Pressable>
          {evac.emergency_active && (
            <Text style={styles.evacEmergencyText}>🚨 Emergency near {evac.emergency_zone || "the venue"}.</Text>
          )}
          {evac.routes[0] ? (
            <Text style={styles.evacText}>
              {evac.emergency_active ? "Nearest safe exit: " : "Recommended exit: "}
              <Text style={{ fontWeight: "800" }}>{evac.routes[0].name}</Text>
              {evac.routes[0].distance_km != null ? ` (${evac.routes[0].distance_km} km)` : ""}
              {evac.routes[0].is_accessible ? " ♿" : ""}
              {evac.all_exits_congested ? " — all exits under pressure, proceed with caution" : ""}
              {!evac.from_attendee_location ? " · distance from venue centre — enable location for distance from you" : ""}
            </Text>
          ) : (
            <Text style={styles.mutedText}>
              {accessibleOnly ? "No accessible exit configured yet — ask a staff member for assistance." : "No gate data yet."}
            </Text>
          )}
        </View>

        {!email ? (
          <View style={styles.card}>
            <Text style={styles.cardTitle}>Your Events</Text>
            <Text style={styles.mutedText}>Sign in to register for events and switch between them.</Text>
            <TextInput
              style={styles.input}
              placeholder="you@example.com"
              placeholderTextColor={colors.muted}
              autoCapitalize="none"
              value={emailInput}
              onChangeText={setEmailInput}
            />
            <Pressable style={styles.secondaryBtn} onPress={continueWithEmail}>
              <Text style={styles.secondaryBtnText}>CONTINUE</Text>
            </Pressable>
          </View>
        ) : (
          <View style={styles.card}>
            <Text style={styles.cardTitle}>Your Events</Text>
            {myEvents.map((e) => (
              <View key={e.event_attendee_id} style={styles.myEventRow}>
                <View>
                  <Text style={styles.rowLabel}>
                    {e.event_name} {e.is_live ? <Text style={{ color: colors.ok, fontSize: 10 }}>LIVE</Text> : null}
                  </Text>
                  <Text style={styles.mutedText}>{e.registration_status}</Text>
                </View>
                {e.is_current ? (
                  <Text style={styles.currentTag}>CURRENT</Text>
                ) : (
                  <Pressable style={styles.switchBtn} onPress={() => switchEvent(e.event_attendee_id)}>
                    <Text style={styles.switchBtnText}>Switch</Text>
                  </Pressable>
                )}
              </View>
            ))}
            {!myEvents.length && <Text style={styles.mutedText}>Not registered for any events yet.</Text>}
            <Pressable style={styles.primaryBtn} onPress={registerForCurrent}>
              <Text style={styles.primaryBtnText}>REGISTER FOR CURRENT EVENT</Text>
            </Pressable>
          </View>
        )}

        <View style={styles.card}>
          <Text style={styles.cardTitle}>Gate status — spots remaining</Text>
          {advisory.gates.map((g) => (
            <View key={g.id} style={styles.gateRow}>
              <View style={{ flex: 1 }}>
                <View style={styles.gateHeader}>
                  <View style={[styles.dot, { backgroundColor: levelColor[g.level] }]} />
                  <Text style={styles.rowLabel}>{g.name}</Text>
                  <Text style={styles.mutedText}>{g.remaining.toLocaleString()} spots left</Text>
                </View>
                <View style={styles.gauge}>
                  <View style={[styles.gaugeFill, { width: `${Math.min(g.capacity_pressure_pct, 100)}%`, backgroundColor: levelColor[g.level] }]} />
                </View>
              </View>
            </View>
          ))}
          {!!aiText && <Text style={styles.aiText}>✨ {aiText}</Text>}
        </View>

        {!!offPeak.length && (
          <View style={styles.card}>
            <Text style={styles.cardTitle}>🕒 Best time to arrive</Text>
            {offPeak.map((o, i) => (
              <View key={i} style={styles.listRow}>
                <View style={[styles.dot, { backgroundColor: levelColor[o.current_level] }]} />
                <Text style={[styles.mutedText, { flex: 1, color: colors.ink }]}>{o.recommendation}</Text>
              </View>
            ))}
          </View>
        )}
      </ScrollView>
      <ChatFab />
    </View>
  );
}

const styles = StyleSheet.create({
  backText: { color: colors.muted, fontSize: 12, marginBottom: spacing.sm },
  title: { fontSize: 22, fontWeight: "900", color: colors.ink },
  subtitle: { fontSize: 12, color: colors.muted, marginTop: 4, marginBottom: spacing.md },
  alertBanner: {
    flexDirection: "row",
    gap: 10,
    backgroundColor: "#FEF2F2",
    borderWidth: 1.5,
    borderColor: colors.high,
    borderRadius: radius.lg,
    padding: spacing.md,
    marginBottom: spacing.md,
  },
  alertIcon: { fontSize: 22 },
  alertTitle: { fontWeight: "900", fontSize: 12, color: colors.danger },
  alertText: { fontSize: 12.5, color: colors.ink, marginTop: 2, lineHeight: 17 },
  statRow: { flexDirection: "row", gap: spacing.md, marginBottom: spacing.md },
  statTile: { flex: 1, backgroundColor: colors.panel, borderRadius: radius.lg, borderWidth: 1, borderColor: colors.border, padding: spacing.md, alignItems: "center" },
  statLabel: { fontSize: 10, fontWeight: "800", color: colors.ink, textTransform: "uppercase", letterSpacing: 1 },
  statValue: { fontSize: 18, fontWeight: "800", marginTop: 4 },
  card: { backgroundColor: colors.panel, borderRadius: radius.lg, borderWidth: 1, borderColor: colors.border, padding: spacing.md, marginBottom: spacing.md, ...shadow.card },
  evacCardEmergency: { borderColor: colors.high, borderWidth: 1.5, backgroundColor: "#FEF2F2" },
  accessToggle: { flexDirection: "row", alignItems: "center", gap: 8, marginBottom: spacing.sm },
  checkbox: { width: 18, height: 18, borderRadius: 4, borderWidth: 1.5, borderColor: colors.border, alignItems: "center", justifyContent: "center" },
  checkboxOn: { backgroundColor: colors.accent, borderColor: colors.accent },
  checkboxMark: { color: "#fff", fontSize: 12, fontWeight: "800" },
  evacEmergencyText: { color: colors.danger, fontWeight: "800", fontSize: 12.5, marginBottom: 4 },
  evacText: { fontSize: 12.5, color: colors.ink, lineHeight: 17 },
  cardTitle: { fontSize: 11, fontWeight: "800", letterSpacing: 1, color: colors.ink, textTransform: "uppercase", marginBottom: spacing.sm },
  mutedText: { fontSize: 12, color: colors.muted },
  input: { borderWidth: 1, borderColor: colors.border, borderRadius: radius.md, padding: 10, color: colors.ink, marginVertical: spacing.sm },
  secondaryBtn: { backgroundColor: colors.pastelBlue, borderRadius: radius.md, paddingVertical: 10, alignItems: "center" },
  secondaryBtnText: { color: colors.ink, fontWeight: "800", fontSize: 11 },
  primaryBtn: { backgroundColor: colors.accent, borderRadius: radius.md, paddingVertical: 10, alignItems: "center", marginTop: spacing.sm },
  primaryBtnText: { color: "#fff", fontWeight: "800", fontSize: 11 },
  myEventRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", paddingVertical: 8, borderBottomWidth: 1, borderBottomColor: colors.border },
  currentTag: { color: colors.accent, fontSize: 10, fontWeight: "800" },
  switchBtn: { backgroundColor: colors.pastelBlue, borderRadius: radius.md, paddingVertical: 6, paddingHorizontal: 10 },
  switchBtnText: { color: colors.ink, fontSize: 10.5, fontWeight: "700" },
  rowLabel: { fontSize: 13, fontWeight: "700", color: colors.ink },
  gateRow: { paddingVertical: 8 },
  gateHeader: { flexDirection: "row", alignItems: "center", gap: 8, justifyContent: "space-between" },
  dot: { width: 9, height: 9, borderRadius: 5 },
  gauge: { height: 6, borderRadius: 4, backgroundColor: colors.bg, marginTop: 6, overflow: "hidden" },
  gaugeFill: { height: "100%", borderRadius: 4 },
  aiText: { color: colors.accent, fontSize: 12.5, marginTop: spacing.sm, lineHeight: 17 },
  listRow: { flexDirection: "row", alignItems: "flex-start", gap: 8, paddingVertical: 6 },
});
