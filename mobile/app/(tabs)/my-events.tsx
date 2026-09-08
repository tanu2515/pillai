import { useCallback, useEffect, useState } from "react";
import { View, Text, TextInput, Pressable, FlatList, StyleSheet, Modal } from "react-native";
import { useFocusEffect, router } from "expo-router";
import QRCode from "react-native-qrcode-svg";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { Header } from "../../src/components/Header";
import { api, apiPost, getEmail, setEmail as saveEmail } from "../../src/api";
import { colors, radius, spacing, shadow, levelColor } from "../../src/theme";
import { openInMaps } from "../../src/maps";

type Booking = {
  code: string;
  event_name: string;
  event_date?: string;
  venue_name?: string;
  tier_name: string;
  seat_label?: string;
  quantity: number;
  checked_in: boolean;
  gate_name?: string;
  hotel_name?: string;
  wants_transport: boolean;
};
type Bookings = { upcoming: Booking[]; active: Booking[]; past: Booking[] };
const TABS: (keyof Bookings)[] = ["upcoming", "active", "past"];

type PlanGate = { name: string; level: string; capacity_pressure_pct: number; lat: number | null; lng: number | null };
type PlanHotel = { name: string; available_pct: number; lat: number | null; lng: number | null; reason: string };
type PlanTransport = { zone_name: string; recommendation: string };
type PlanArrival = { recommendation: string };
type Plan = {
  event_id: number;
  event_name: string;
  is_live: boolean;
  gate: PlanGate | null;
  hotel: PlanHotel | null;
  transport: PlanTransport | null;
  arrival: PlanArrival | null;
};

type Notif = { id: number; title: string; message: string; priority: string; is_read: boolean };
const NOTIF_ICON: Record<string, string> = { CRITICAL: "🔴", HIGH: "🟠", MEDIUM: "🟡", LOW: "🟢" };
// Attendee-friendly words instead of raw risk-engine terminology — the
// underlying level/number is unchanged, only the label shown is translated.
const LEVEL_LABEL: Record<string, string> = { LOW: "Quiet", MODERATE: "Moderate crowd", HIGH: "Busy", CRITICAL: "Very busy" };

type EvacRoute = { id: number; name: string; distance_km: number | null; is_accessible: boolean };
type Evacuation = { emergency_active: boolean; emergency_zone: string | null; routes: EvacRoute[] };
type Hotel = { id: number; name: string; available_rooms: number; distance_km: number | null; last_updated: string | null };
type TransportItem = { route?: string; line?: string; airline?: string; description?: string; destination?: string; origin?: string; arrives_in_min: number };
type TransportData = { local?: { buses?: { city?: TransportItem[] }; trains?: { suburban?: TransportItem[] } }; flights?: { arrivals?: TransportItem[] } };

export default function MyEvents() {
  const [email, setEmailState] = useState("");
  const [emailInput, setEmailInput] = useState("");
  const [bookings, setBookings] = useState<Bookings>({ upcoming: [], active: [], past: [] });
  const [tab, setTab] = useState<keyof Bookings>("upcoming");
  const [qrCode, setQrCode] = useState<string | null>(null);
  const [plans, setPlans] = useState<Record<string, Plan>>({});
  const [notifs, setNotifs] = useState<Notif[]>([]);
  const [anyLiveActive, setAnyLiveActive] = useState(false);
  const [liveEventId, setLiveEventId] = useState<number | null>(null);
  const [accessibleOnly, setAccessibleOnly] = useState(false);
  const [evac, setEvac] = useState<Evacuation | null>(null);
  const [hotels, setHotels] = useState<Hotel[]>([]);
  const [transport, setTransport] = useState<TransportData>({});
  const [liveCrowdText, setLiveCrowdText] = useState<string | null>(null);

  useEffect(() => {
    AsyncStorage.getItem("vyavastha_pref_accessible").then((v) => {
      if (v === "1") setAccessibleOnly(true);
    });
  }, []);

  // Live event info (crowd guidance, accessibility/evacuation, hotels,
  // transport) is only fetched/shown when the attendee's own active booking
  // is for the event that's actually live right now (plan.is_live from
  // /api/my-plan), and is always scoped by that booking's own event_id —
  // never "whatever event happens to be live" independent of this
  // attendee's own booking.
  const loadLiveInfo = useCallback(async (accessible: boolean, eventId: number | null) => {
    try {
      const eventQuery = eventId != null ? `event_id=${eventId}` : "";
      const [evacData, hotelData, transportData, advisory] = await Promise.all([
        api<Evacuation>(`/api/evacuation-routes?accessible_only=${accessible}${eventQuery ? `&${eventQuery}` : ""}`),
        api<{ hotels: Hotel[] }>(`/api/attendee/hotels${eventQuery ? `?${eventQuery}` : ""}`),
        api<TransportData>(`/api/attendee/transport${eventQuery ? `?${eventQuery}` : ""}`),
        api<{ text: string | null }>("/api/ai/attendee-advisory"),
      ]);
      setEvac(evacData);
      setHotels(hotelData.hotels || []);
      setTransport(transportData);
      setLiveCrowdText(advisory.text);
    } catch {
      // best-effort — leave previous values in place
    }
  }, []);

  const load = useCallback(async () => {
    const stored = await getEmail();
    setEmailState(stored);
    if (!stored) return;
    const data = await api<Bookings>(`/api/my-bookings?email=${encodeURIComponent(stored)}`);
    setBookings(data);

    if (data.active.length) {
      const entries = await Promise.all(
        data.active.map(async (b) => {
          try {
            return [b.code, await api<Plan>(`/api/my-plan?code=${b.code}`)] as const;
          } catch {
            return null;
          }
        })
      );
      const planMap = Object.fromEntries(entries.filter((e): e is readonly [string, Plan] => e !== null));
      setPlans(planMap);
      const livePlan = Object.values(planMap).find((p) => p.is_live);
      setAnyLiveActive(!!livePlan);
      const eventId = livePlan ? livePlan.event_id : null;
      setLiveEventId(eventId);
      if (livePlan) await loadLiveInfo(accessibleOnly, eventId);
      try {
        // Scoped to the attendee's own live event (falls back to including
        // event-agnostic system notices) — never another event's feed mixed in.
        const notifQuery = eventId != null ? `&event_id=${eventId}` : "";
        setNotifs(await api<Notif[]>(`/api/notifications?role=Attendee${notifQuery}`));
      } catch {
        setNotifs([]);
      }
    } else {
      setPlans({});
      setNotifs([]);
      setAnyLiveActive(false);
      setLiveEventId(null);
    }
  }, [accessibleOnly, loadLiveInfo]);

  async function toggleAccessible() {
    const next = !accessibleOnly;
    setAccessibleOnly(next);
    const em = await getEmail();
    await apiPost("/api/attendee/accessibility-request", { email: em || null });
    if (anyLiveActive) await loadLiveInfo(next, liveEventId);
  }

  useFocusEffect(
    useCallback(() => {
      load();
      const interval = setInterval(load, 8000);
      return () => clearInterval(interval);
    }, [load])
  );

  async function continueWithEmail() {
    if (!emailInput.trim()) return;
    await saveEmail(emailInput.trim());
    await load();
  }

  if (!email) {
    return (
      <View style={styles.container}>
        <Header title="My Event" />
        <View style={styles.signInCard}>
          <Text style={styles.signInText}>Sign in to see your bookings.</Text>
          <TextInput
            style={styles.input}
            placeholder="you@example.com"
            placeholderTextColor={colors.muted}
            autoCapitalize="none"
            value={emailInput}
            onChangeText={setEmailInput}
          />
          <Pressable style={styles.btn} onPress={continueWithEmail}>
            <Text style={styles.btnText}>CONTINUE</Text>
          </Pressable>
        </View>
      </View>
    );
  }

  const list = bookings[tab];

  return (
    <View style={styles.container}>
      <Header title="My Event" />
      <View style={styles.tabRow}>
        {TABS.map((t) => (
          <Pressable key={t} style={[styles.tabBtn, tab === t && styles.tabBtnActive]} onPress={() => setTab(t)}>
            <Text style={[styles.tabText, tab === t && styles.tabTextActive]}>{t[0].toUpperCase() + t.slice(1)}</Text>
          </Pressable>
        ))}
      </View>
      <FlatList
        data={list}
        keyExtractor={(b) => b.code}
        contentContainerStyle={{ padding: spacing.lg, gap: spacing.md }}
        ListEmptyComponent={<Text style={styles.empty}>No {tab} events yet.</Text>}
        ListFooterComponent={
          tab === "active" ? (
            <>
              {anyLiveActive && (
                <View style={styles.liveInfoBox}>
                  <Text style={styles.notifTitle}>🔴 Live Crowd</Text>
                  <Text style={styles.smallMeta}>{liveCrowdText || "All gates are moving smoothly right now."}</Text>
                </View>
              )}
              {anyLiveActive && (
                <View style={styles.liveInfoBox}>
                  <Text style={styles.notifTitle}>🗺️ Inside the venue — accessibility &amp; emergency exits</Text>
                  <Pressable style={styles.rowBetween} onPress={toggleAccessible}>
                    <Text style={styles.smallMeta}>I need a wheelchair-accessible exit</Text>
                    <Text style={{ fontWeight: "800", color: accessibleOnly ? colors.accent : colors.muted }}>{accessibleOnly ? "✓ ON" : "OFF"}</Text>
                  </Pressable>
                  {evac && (
                    evac.emergency_active ? (
                      <Text style={[styles.smallMeta, { color: colors.danger, fontWeight: "800" }]}>
                        🚨 Emergency near {evac.emergency_zone || "the venue"}
                        {evac.routes[0] ? ` — nearest safe exit: ${evac.routes[0].name}` : ""}
                      </Text>
                    ) : evac.routes[0] ? (
                      <Text style={styles.smallMeta}>
                        Recommended exit: {evac.routes[0].name}
                        {evac.routes[0].distance_km != null ? ` (${evac.routes[0].distance_km} km)` : ""}
                        {evac.routes[0].is_accessible ? " ♿" : ""}
                      </Text>
                    ) : (
                      <Text style={styles.smallMeta}>No gate data yet.</Text>
                    )
                  )}

                  <Text style={[styles.notifTitle, { marginTop: spacing.md }]}>🏨 More hotels near the venue</Text>
                  {hotels.length ? hotels.slice(0, 3).map((h) => (
                    <Text key={h.id} style={styles.smallMeta}>
                      {h.name} — {h.available_rooms} rooms free{h.distance_km != null ? ` · ${h.distance_km} km` : ""} · {h.last_updated ? "LIVE" : "DEMO"}
                    </Text>
                  )) : <Text style={styles.smallMeta}>No connected hotel inventory yet.</Text>}

                  <Text style={[styles.notifTitle, { marginTop: spacing.md }]}>🚌 City transport (not event-specific)</Text>
                  {(transport.local?.buses?.city || []).slice(0, 2).map((x, i) => (
                    <Text key={`bus-${i}`} style={styles.smallMeta}>🚌 {x.route}: {x.arrives_in_min} min</Text>
                  ))}
                  {(transport.local?.trains?.suburban || []).slice(0, 2).map((x, i) => (
                    <Text key={`train-${i}`} style={styles.smallMeta}>🚆 {x.line}: {x.arrives_in_min} min</Text>
                  ))}
                  {(transport.flights?.arrivals || []).slice(0, 2).map((x, i) => (
                    <Text key={`flight-${i}`} style={styles.smallMeta}>✈️ {x.airline}: {x.arrives_in_min} min</Text>
                  ))}
                  <Text style={[styles.smallMeta, { marginTop: 4, fontStyle: "italic" }]}>Buses/trains/flights above are city/region-level schedules (illustrative, not live operator tracking) — not specific to this event.</Text>
                </View>
              )}
              {!!notifs.length && (
                <View style={styles.notifBox}>
                  <View style={styles.rowBetween}>
                    <Text style={styles.notifTitle}>
                      🔔 {notifs.filter((n) => !n.is_read).length} unread alert{notifs.filter((n) => !n.is_read).length === 1 ? "" : "s"} for attendees
                    </Text>
                    <Pressable onPress={() => router.push("/(tabs)/notifications")}>
                      <Text style={styles.navLink}>View all in Alerts →</Text>
                    </Pressable>
                  </View>
                </View>
              )}
            </>
          ) : null
        }
        renderItem={({ item }) => {
          const plan = plans[item.code];
          return (
            <View style={styles.card}>
              <View style={styles.cardHeader}>
                <Text style={styles.eventName}>{item.event_name}</Text>
                {item.checked_in && <Text style={styles.checkedPill}>✓ CHECKED IN</Text>}
              </View>
              <Text style={styles.meta}>{item.event_date ? new Date(item.event_date).toDateString() : "Date TBA"}</Text>
              <Text style={styles.meta}>{item.venue_name || ""}</Text>
              <View style={styles.rowBetween}>
                <Text style={styles.tierLine}>
                  {item.tier_name}
                  {item.seat_label ? ` · Seat ${item.seat_label}` : ""}
                  {item.quantity > 1 ? ` · Qty ${item.quantity}` : ""}
                </Text>
                <Text style={styles.gate}>{item.gate_name || ""}</Text>
              </View>
              {!!item.hotel_name && <Text style={styles.smallMeta}>🏨 {item.hotel_name}</Text>}
              {item.wants_transport && <Text style={styles.smallMeta}>🚌 Transport requested</Text>}
              <Pressable style={styles.qrBtn} onPress={() => setQrCode(item.code)}>
                <Text style={styles.qrBtnText}>VIEW QR CODE — {item.code}</Text>
              </Pressable>

              {tab === "active" && plan?.is_live && plan.gate && (
                <View style={styles.planBox}>
                  <Text style={styles.notifTitle}>📍 Your Gate</Text>
                  <View style={styles.rowBetween}>
                    <Text style={styles.smallMeta}>
                      {plan.gate.name} · <Text style={{ color: levelColor[plan.gate.level] }}>{LEVEL_LABEL[plan.gate.level] || plan.gate.level}</Text>
                    </Text>
                    {plan.gate.lat != null && plan.gate.lng != null && (
                      <Pressable onPress={() => openInMaps(plan.gate!.lat!, plan.gate!.lng!)}>
                        <Text style={styles.navLink}>🧭 Navigate</Text>
                      </Pressable>
                    )}
                  </View>
                </View>
              )}
              {tab === "active" && plan?.is_live && (plan.hotel || plan.transport || plan.arrival) && (
                <View style={styles.planBox}>
                  <Text style={styles.notifTitle}>🧭 Your Journey</Text>
                  {plan.hotel && (
                    <View style={styles.rowBetween}>
                      <Text style={styles.smallMeta}>🏨 {plan.hotel.name}</Text>
                      {plan.hotel.lat != null && plan.hotel.lng != null && (
                        <Pressable onPress={() => openInMaps(plan.hotel!.lat!, plan.hotel!.lng!)}>
                          <Text style={styles.navLink}>🧭 Navigate</Text>
                        </Pressable>
                      )}
                    </View>
                  )}
                  {plan.transport && <Text style={styles.smallMeta}>🚍 {plan.transport.recommendation}</Text>}
                  {plan.arrival && <Text style={styles.smallMeta}>🕒 {plan.arrival.recommendation}</Text>}
                </View>
              )}
            </View>
          );
        }}
      />
      <Modal visible={!!qrCode} transparent animationType="fade" onRequestClose={() => setQrCode(null)}>
        <View style={styles.qrOverlay}>
          <View style={styles.qrBox}>
            {!!qrCode && <QRCode value={qrCode} size={160} color={colors.ink} backgroundColor="#fff" />}
            <Text style={styles.qrCodeText}>{qrCode}</Text>
            <Pressable onPress={() => setQrCode(null)}>
              <Text style={styles.qrClose}>Close</Text>
            </Pressable>
          </View>
        </View>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  signInCard: { margin: spacing.lg, backgroundColor: colors.panel, borderRadius: radius.lg, padding: spacing.lg, borderWidth: 1, borderColor: colors.border },
  signInText: { fontSize: 13, color: colors.ink, marginBottom: spacing.sm },
  input: { borderWidth: 1, borderColor: colors.border, borderRadius: radius.md, padding: 10, color: colors.ink, marginBottom: spacing.sm },
  btn: { backgroundColor: colors.accent, borderRadius: radius.md, paddingVertical: 10, alignItems: "center" },
  btnText: { color: "#fff", fontWeight: "800", fontSize: 12 },
  tabRow: { flexDirection: "row", gap: spacing.sm, paddingHorizontal: spacing.lg, marginTop: spacing.sm },
  tabBtn: { paddingVertical: 8, paddingHorizontal: 16, borderRadius: radius.pill, backgroundColor: colors.panel, borderWidth: 1, borderColor: colors.border },
  tabBtnActive: { backgroundColor: colors.accentDim, borderColor: colors.accentDim },
  tabText: { fontSize: 12, fontWeight: "800", color: colors.ink },
  tabTextActive: { color: "#fff" },
  card: { backgroundColor: colors.panel, borderRadius: radius.lg, borderWidth: 1, borderColor: colors.border, padding: spacing.md, ...shadow.card },
  cardHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  eventName: { fontWeight: "800", fontSize: 14, color: colors.ink, flex: 1 },
  checkedPill: { fontSize: 9, fontWeight: "800", color: colors.green },
  meta: { fontSize: 10.5, color: colors.muted, marginTop: 2 },
  rowBetween: { flexDirection: "row", justifyContent: "space-between", marginTop: 8 },
  tierLine: { fontSize: 12, color: colors.ink },
  gate: { fontSize: 11, color: colors.muted },
  smallMeta: { fontSize: 10.5, color: colors.muted, marginTop: 4 },
  qrBtn: { backgroundColor: colors.pastelBlue, borderRadius: radius.md, paddingVertical: 10, alignItems: "center", marginTop: spacing.sm },
  qrBtnText: { color: colors.ink, fontWeight: "700", fontSize: 11 },
  planBox: { backgroundColor: colors.bg, borderRadius: radius.md, padding: spacing.sm, marginTop: spacing.sm, gap: 4 },
  navLink: { color: colors.accent, fontSize: 10.5, fontWeight: "800" },
  notifBox: { marginHorizontal: spacing.lg, marginTop: spacing.sm, backgroundColor: colors.panel, borderRadius: radius.lg, borderWidth: 1, borderColor: colors.border, padding: spacing.md, ...shadow.card },
  liveInfoBox: { marginHorizontal: spacing.lg, marginTop: spacing.sm, backgroundColor: colors.panel, borderRadius: radius.lg, borderWidth: 1, borderColor: colors.border, padding: spacing.md, gap: 2, ...shadow.card },
  notifTitle: { fontSize: 10.5, fontWeight: "800", letterSpacing: 1, color: colors.ink, textTransform: "uppercase", marginBottom: 6 },
  notifRow: { flexDirection: "row", alignItems: "flex-start", gap: 8, paddingVertical: 4 },
  notifMsg: { flex: 1, fontSize: 12, color: colors.ink },
  empty: { color: colors.muted, fontSize: 12, textAlign: "center", marginTop: 40 },
  qrOverlay: { flex: 1, backgroundColor: "rgba(18,59,109,0.5)", alignItems: "center", justifyContent: "center" },
  qrBox: { backgroundColor: "#fff", borderRadius: radius.xl, padding: spacing.xl, alignItems: "center" },
  qrCodeText: { fontSize: 20, letterSpacing: 4, fontWeight: "800", color: colors.accent, marginTop: spacing.md },
  qrClose: { color: colors.muted, fontSize: 12, fontWeight: "800", marginTop: spacing.md, textTransform: "uppercase" },
});
