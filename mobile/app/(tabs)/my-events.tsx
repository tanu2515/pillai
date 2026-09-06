import { useCallback, useState } from "react";
import { View, Text, TextInput, Pressable, FlatList, StyleSheet, Modal } from "react-native";
import { useFocusEffect } from "expo-router";
import QRCode from "react-native-qrcode-svg";
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
  event_name: string;
  is_live: boolean;
  gate: PlanGate | null;
  hotel: PlanHotel | null;
  transport: PlanTransport | null;
  arrival: PlanArrival | null;
};

type Notif = { id: number; title: string; message: string; priority: string; is_read: boolean };
const NOTIF_ICON: Record<string, string> = { CRITICAL: "🔴", HIGH: "🟠", MEDIUM: "🟡", LOW: "🟢" };

export default function MyEvents() {
  const [email, setEmailState] = useState("");
  const [emailInput, setEmailInput] = useState("");
  const [bookings, setBookings] = useState<Bookings>({ upcoming: [], active: [], past: [] });
  const [tab, setTab] = useState<keyof Bookings>("upcoming");
  const [qrCode, setQrCode] = useState<string | null>(null);
  const [plans, setPlans] = useState<Record<string, Plan>>({});
  const [notifs, setNotifs] = useState<Notif[]>([]);

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
      setPlans(Object.fromEntries(entries.filter((e): e is readonly [string, Plan] => e !== null)));
      try {
        setNotifs((await api<Notif[]>("/api/notifications?role=Attendee")).slice(0, 3));
      } catch {
        setNotifs([]);
      }
    } else {
      setPlans({});
      setNotifs([]);
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      load();
      const interval = setInterval(load, 8000);
      return () => clearInterval(interval);
    }, [load])
  );

  async function markNotifRead(id: number) {
    await apiPost(`/api/notifications/${id}/read`, {});
    setNotifs((prev) => prev.filter((n) => n.id !== id));
  }

  async function continueWithEmail() {
    if (!emailInput.trim()) return;
    await saveEmail(emailInput.trim());
    await load();
  }

  if (!email) {
    return (
      <View style={styles.container}>
        <Header title="My Events" />
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
      <Header title="My Events" />
      <View style={styles.tabRow}>
        {TABS.map((t) => (
          <Pressable key={t} style={[styles.tabBtn, tab === t && styles.tabBtnActive]} onPress={() => setTab(t)}>
            <Text style={[styles.tabText, tab === t && styles.tabTextActive]}>{t[0].toUpperCase() + t.slice(1)}</Text>
          </Pressable>
        ))}
      </View>
      {tab === "active" && !!notifs.length && (
        <View style={styles.notifBox}>
          <Text style={styles.notifTitle}>🔔 Notifications</Text>
          {notifs.map((n) => (
            <Pressable key={n.id} style={styles.notifRow} onPress={() => markNotifRead(n.id)}>
              <Text style={{ fontSize: 13 }}>{NOTIF_ICON[n.priority] || "🟡"}</Text>
              <Text style={styles.notifMsg} numberOfLines={2}>{n.message}</Text>
            </Pressable>
          ))}
        </View>
      )}
      <FlatList
        data={list}
        keyExtractor={(b) => b.code}
        contentContainerStyle={{ padding: spacing.lg, gap: spacing.md }}
        ListEmptyComponent={<Text style={styles.empty}>No {tab} events yet.</Text>}
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

              {tab === "active" && plan?.is_live && (
                <View style={styles.planBox}>
                  <Text style={styles.notifTitle}>Your Plan</Text>
                  {plan.gate && (
                    <View style={styles.rowBetween}>
                      <Text style={styles.smallMeta}>
                        Gate: {plan.gate.name} · <Text style={{ color: levelColor[plan.gate.level] }}>{plan.gate.level}</Text>
                      </Text>
                      {plan.gate.lat != null && plan.gate.lng != null && (
                        <Pressable onPress={() => openInMaps(plan.gate!.lat!, plan.gate!.lng!)}>
                          <Text style={styles.navLink}>🧭 Navigate</Text>
                        </Pressable>
                      )}
                    </View>
                  )}
                  {plan.hotel && (
                    <View style={styles.rowBetween}>
                      <Text style={styles.smallMeta}>Hotel: {plan.hotel.name}</Text>
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
  notifTitle: { fontSize: 10.5, fontWeight: "800", letterSpacing: 1, color: colors.ink, textTransform: "uppercase", marginBottom: 6 },
  notifRow: { flexDirection: "row", alignItems: "flex-start", gap: 8, paddingVertical: 4 },
  notifMsg: { flex: 1, fontSize: 12, color: colors.ink },
  empty: { color: colors.muted, fontSize: 12, textAlign: "center", marginTop: 40 },
  qrOverlay: { flex: 1, backgroundColor: "rgba(18,59,109,0.5)", alignItems: "center", justifyContent: "center" },
  qrBox: { backgroundColor: "#fff", borderRadius: radius.xl, padding: spacing.xl, alignItems: "center" },
  qrCodeText: { fontSize: 20, letterSpacing: 4, fontWeight: "800", color: colors.accent, marginTop: spacing.md },
  qrClose: { color: colors.muted, fontSize: 12, fontWeight: "800", marginTop: spacing.md, textTransform: "uppercase" },
});
