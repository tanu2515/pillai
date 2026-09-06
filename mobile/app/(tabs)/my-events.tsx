import { useCallback, useState } from "react";
import { View, Text, TextInput, Pressable, FlatList, StyleSheet, Modal } from "react-native";
import { useFocusEffect } from "expo-router";
import QRCode from "react-native-qrcode-svg";
import { Header } from "../../src/components/Header";
import { api, getEmail, setEmail as saveEmail } from "../../src/api";
import { colors, radius, spacing, shadow } from "../../src/theme";

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

export default function MyEvents() {
  const [email, setEmailState] = useState("");
  const [emailInput, setEmailInput] = useState("");
  const [bookings, setBookings] = useState<Bookings>({ upcoming: [], active: [], past: [] });
  const [tab, setTab] = useState<keyof Bookings>("upcoming");
  const [qrCode, setQrCode] = useState<string | null>(null);

  const load = useCallback(async () => {
    const stored = await getEmail();
    setEmailState(stored);
    if (!stored) return;
    const data = await api<Bookings>(`/api/my-bookings?email=${encodeURIComponent(stored)}`);
    setBookings(data);
  }, []);

  useFocusEffect(
    useCallback(() => {
      load();
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
      <FlatList
        data={list}
        keyExtractor={(b) => b.code}
        contentContainerStyle={{ padding: spacing.lg, gap: spacing.md }}
        ListEmptyComponent={<Text style={styles.empty}>No {tab} events yet.</Text>}
        renderItem={({ item }) => (
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
          </View>
        )}
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
  empty: { color: colors.muted, fontSize: 12, textAlign: "center", marginTop: 40 },
  qrOverlay: { flex: 1, backgroundColor: "rgba(18,59,109,0.5)", alignItems: "center", justifyContent: "center" },
  qrBox: { backgroundColor: "#fff", borderRadius: radius.xl, padding: spacing.xl, alignItems: "center" },
  qrCodeText: { fontSize: 20, letterSpacing: 4, fontWeight: "800", color: colors.accent, marginTop: spacing.md },
  qrClose: { color: colors.muted, fontSize: 12, fontWeight: "800", marginTop: spacing.md, textTransform: "uppercase" },
});
