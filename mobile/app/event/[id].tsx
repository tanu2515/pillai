import { useEffect, useState } from "react";
import {
  View,
  Text,
  ScrollView,
  Pressable,
  TextInput,
  StyleSheet,
  Modal,
  ActivityIndicator,
} from "react-native";
import { useLocalSearchParams, router } from "expo-router";
import QRCode from "react-native-qrcode-svg";
import { api, apiPost, getEmail } from "../../src/api";
import { colors, radius, spacing, shadow, levelColor } from "../../src/theme";
import { openInMaps } from "../../src/maps";

type Tier = { id: number; name: string; price: number; capacity: number; available: number; uses_seats: boolean; gate_name?: string };
type Gate = { name: string; occupancy_pct: number; level: string };
type Hotel = { zone_id: number; name: string; available_pct: number; distance_km?: number; recommended: boolean };
type Transport = { zone_name: string; current_pct: number; extra_buses_needed: number };
type Announcement = { severity: string; message: string };
type OffPeak = { recommendation: string; current_level?: string };
type EventDetail = {
  id: number;
  name: string;
  description?: string;
  event_date?: string;
  event_time?: string;
  venue_name?: string;
  venue_address?: string;
  banner_emoji?: string;
  is_live: boolean;
  expected_attendance: number;
  safe_capacity: number;
  gates: Gate[];
  transport_info: Transport[] | null;
  hotels: Hotel[];
  announcements: Announcement[];
  off_peak: OffPeak[];
  tiers: Tier[];
};
type Seat = { id: number; seat_label: string; status: string };
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

function fmtDate(dateStr?: string, timeStr?: string) {
  if (!dateStr) return "Date to be announced";
  const d = new Date(dateStr);
  const s = d.toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
  return timeStr ? `${s} · ${timeStr}` : s;
}

export default function EventDetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const [event, setEvent] = useState<EventDetail | null>(null);
  const [selectedTier, setSelectedTier] = useState<Tier | null>(null);
  const [bookingOpen, setBookingOpen] = useState(false);

  async function load() {
    const data = await api<EventDetail>(`/api/events/${id}`);
    setEvent(data);
  }

  useEffect(() => {
    load();
  }, [id]);

  if (!event) {
    return (
      <View style={styles.loading}>
        <ActivityIndicator color={colors.accent} />
      </View>
    );
  }

  return (
    <View style={{ flex: 1, backgroundColor: colors.bg }}>
      <View style={styles.topBar}>
        <Pressable onPress={() => router.back()}>
          <Text style={styles.backText}>← All Events</Text>
        </Pressable>
      </View>
      <ScrollView contentContainerStyle={{ paddingBottom: 100 }}>
        <View style={styles.banner}>
          <Text style={{ fontSize: 56 }}>{event.banner_emoji || "🎉"}</Text>
        </View>

        <View style={styles.card}>
          <View style={styles.row}>
            <Text style={styles.eventName}>{event.name}</Text>
            {event.is_live && (
              <View style={styles.livePill}>
                <Text style={styles.liveText}>LIVE</Text>
              </View>
            )}
          </View>
          <Text style={styles.datetime}>{fmtDate(event.event_date, event.event_time)}</Text>
          <Text style={styles.venue}>{event.venue_name || "Venue to be announced"}</Text>
          <Text style={styles.address}>{event.venue_address || ""}</Text>
          <View style={styles.statRow}>
            <Text style={styles.stat}>Expected: {event.expected_attendance.toLocaleString()}</Text>
            <Text style={styles.stat}>Capacity: {event.safe_capacity.toLocaleString()}</Text>
          </View>
        </View>

        <Section title="About this event">
          <Text style={styles.bodyText}>{event.description || "No description provided."}</Text>
        </Section>

        <Section title="Gates & crowd status">
          {event.is_live ? (
            event.gates.length ? (
              event.gates.map((g) => (
                <View key={g.name} style={styles.listRow}>
                  <View style={styles.rowLeft}>
                    <View style={[styles.dot, { backgroundColor: levelColor[g.level] }]} />
                    <Text style={styles.rowLabel}>{g.name}</Text>
                  </View>
                  <Text style={styles.rowValue}>
                    {g.occupancy_pct}% · {g.level}
                  </Text>
                </View>
              ))
            ) : (
              <Text style={styles.mutedText}>No gate data available.</Text>
            )
          ) : (
            <Text style={styles.mutedText}>
              Gate assignment (finalized once the event goes live)
              {event.tiers.some((t) => t.gate_name) ? `: ${[...new Set(event.tiers.map((t) => t.gate_name).filter(Boolean))].join(", ")}` : "."}
            </Text>
          )}
        </Section>

        {!!event.off_peak?.length && (
          <Section title="🕒 Best time to arrive">
            {event.off_peak.map((o, i) => (
              <View key={i} style={styles.listRow}>
                {o.current_level && <View style={[styles.dot, { backgroundColor: levelColor[o.current_level] }]} />}
                <Text style={[styles.bodyText, { flex: 1 }]}>{o.recommendation}</Text>
              </View>
            ))}
          </Section>
        )}

        <Section title="Transport">
          {event.is_live && event.transport_info?.length ? (
            event.transport_info.map((t) => (
              <View key={t.zone_name} style={styles.listRow}>
                <Text style={styles.rowLabel}>{t.zone_name}</Text>
                <Text style={[styles.rowValue, t.extra_buses_needed > 0 && { color: colors.warn }]}>{t.current_pct}% loaded</Text>
              </View>
            ))
          ) : (
            <Text style={styles.mutedText}>Transport info will appear once the event goes live.</Text>
          )}
        </Section>

        <Section title="Nearby hotels">
          {event.is_live && event.hotels.length ? (
            event.hotels.map((h) => (
              <View key={h.name} style={styles.listRow}>
                <Text style={styles.rowLabel}>
                  {h.name} {h.recommended ? "⭐" : ""}
                </Text>
                <Text style={styles.rowValue}>
                  {h.available_pct}% avail.{h.distance_km != null ? ` · ${h.distance_km} km` : ""}
                </Text>
              </View>
            ))
          ) : (
            <Text style={styles.mutedText}>Hotel recommendations will appear once the event goes live.</Text>
          )}
        </Section>

        {!!event.announcements?.length && (
          <Section title="Announcements">
            {event.announcements.map((a, i) => (
              <View key={i} style={styles.listRow}>
                <Text style={{ color: a.severity === "CRITICAL" ? colors.danger : colors.warn }}>●</Text>
                <Text style={[styles.bodyText, { flex: 1, marginLeft: 8 }]}>{a.message}</Text>
              </View>
            ))}
          </Section>
        )}

        <Section title="Select a ticket">
          {event.tiers.length ? (
            event.tiers.map((t) => {
              const soldOut = t.available <= 0;
              const active = selectedTier?.id === t.id;
              return (
                <Pressable
                  key={t.id}
                  disabled={soldOut}
                  onPress={() => setSelectedTier(t)}
                  style={[styles.tierCard, active && styles.tierCardActive, soldOut && styles.tierCardDisabled]}
                >
                  <View>
                    <Text style={styles.tierName}>{t.name}</Text>
                    <Text style={styles.tierMeta}>
                      {t.available.toLocaleString()} / {t.capacity.toLocaleString()} available
                    </Text>
                  </View>
                  <Text style={styles.tierPrice}>{soldOut ? "SOLD OUT" : `₹${t.price.toLocaleString()}`}</Text>
                </Pressable>
              );
            })
          ) : (
            <Text style={styles.mutedText}>No tickets configured for this event yet.</Text>
          )}
        </Section>
      </ScrollView>

      <View style={styles.bottomBar}>
        <Pressable
          style={[styles.registerBtn, !selectedTier && styles.registerBtnDisabled]}
          disabled={!selectedTier}
          onPress={() => setBookingOpen(true)}
        >
          <Text style={styles.registerBtnText}>{selectedTier ? `REGISTER — ${selectedTier.name}` : "SELECT A TICKET TO REGISTER"}</Text>
        </Pressable>
      </View>

      {selectedTier && (
        <BookingSheet
          visible={bookingOpen}
          onClose={() => setBookingOpen(false)}
          eventId={Number(id)}
          eventName={event.name}
          tier={selectedTier}
          hotels={event.hotels}
          hasTransport={!!event.transport_info}
          onBooked={load}
        />
      )}
    </View>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <View style={styles.card}>
      <Text style={styles.sectionTitle}>{title}</Text>
      {children}
    </View>
  );
}

const BUDGET_OPTIONS: { label: string; value: number }[] = [
  { label: "Budget", value: 1 },
  { label: "Mid-range", value: 3 },
  { label: "Premium", value: 5 },
];

function PlanSection({ plan }: { plan: Plan }) {
  if (!plan.is_live) {
    return (
      <View style={styles.planBox}>
        <Text style={styles.planTitle}>Your Plan</Text>
        <Text style={styles.mutedText}>
          {plan.arrival?.recommendation || "Your personalized plan will be ready once the event goes live."}
        </Text>
      </View>
    );
  }
  return (
    <View style={styles.planBox}>
      <Text style={styles.planTitle}>Your Plan</Text>
      {plan.gate && (
        <View style={styles.planRow}>
          <View style={{ flex: 1 }}>
            <Text style={styles.planLabel}>Entry gate</Text>
            <Text style={styles.planValue}>
              {plan.gate.name} · <Text style={{ color: levelColor[plan.gate.level] }}>{plan.gate.level}</Text>
            </Text>
          </View>
          {plan.gate.lat != null && plan.gate.lng != null && (
            <Pressable style={styles.mapBtn} onPress={() => openInMaps(plan.gate!.lat!, plan.gate!.lng!)}>
              <Text style={styles.mapBtnText}>🧭 Navigate</Text>
            </Pressable>
          )}
        </View>
      )}
      {plan.hotel && (
        <View style={styles.planRow}>
          <View style={{ flex: 1 }}>
            <Text style={styles.planLabel}>Recommended hotel</Text>
            <Text style={styles.planValue}>{plan.hotel.name}</Text>
            <Text style={styles.mutedText}>{plan.hotel.reason}</Text>
          </View>
          {plan.hotel.lat != null && plan.hotel.lng != null && (
            <Pressable style={styles.mapBtn} onPress={() => openInMaps(plan.hotel!.lat!, plan.hotel!.lng!)}>
              <Text style={styles.mapBtnText}>🧭 Navigate</Text>
            </Pressable>
          )}
        </View>
      )}
      {plan.transport && (
        <View style={styles.planRowStack}>
          <Text style={styles.planLabel}>Transport</Text>
          <Text style={styles.mutedText}>{plan.transport.recommendation}</Text>
        </View>
      )}
      {plan.arrival && (
        <View style={styles.planRowStack}>
          <Text style={styles.planLabel}>Best time to arrive</Text>
          <Text style={styles.mutedText}>{plan.arrival.recommendation}</Text>
        </View>
      )}
    </View>
  );
}

function BookingSheet({
  visible,
  onClose,
  eventId,
  eventName,
  tier,
  hotels,
  hasTransport,
  onBooked,
}: {
  visible: boolean;
  onClose: () => void;
  eventId: number;
  eventName: string;
  tier: Tier;
  hotels: Hotel[];
  hasTransport: boolean;
  onBooked: () => void;
}) {
  const [name, setName] = useState("");
  const [qty, setQty] = useState(1);
  const [seatId, setSeatId] = useState<number | null>(null);
  const [seats, setSeats] = useState<Seat[]>([]);
  const [showSeats, setShowSeats] = useState(false);
  const [wantsTransport, setWantsTransport] = useState(false);
  const [wantsHotel, setWantsHotel] = useState(false);
  const [budgetTier, setBudgetTier] = useState(3);
  const showHotelOption = hotels.length > 0;
  const [error, setError] = useState("");
  const [result, setResult] = useState<any>(null);
  const [plan, setPlan] = useState<Plan | null>(null);

  useEffect(() => {
    if (visible) {
      setName("");
      setQty(1);
      setSeatId(null);
      setShowSeats(false);
      setWantsTransport(false);
      setWantsHotel(false);
      setBudgetTier(3);
      setError("");
      setResult(null);
      setPlan(null);
    }
  }, [visible, tier.id]);

  async function openSeatPicker() {
    const data = await api<Seat[]>(`/api/events/${eventId}/tiers/${tier.id}/seats`);
    setSeats(data);
    setShowSeats(true);
  }

  async function confirm() {
    if (!name.trim()) {
      setError("Enter your name to continue.");
      return;
    }
    const email = await getEmail();
    const res = await apiPost<any>(`/api/events/${eventId}/tiers/${tier.id}/book`, {
      name: name.trim(),
      email: email || null,
      seat_id: seatId,
      quantity: seatId ? 1 : qty,
      wants_transport: wantsTransport,
      hotel_zone_id: wantsHotel && hotels.length ? hotels[0].zone_id : null,
      budget_tier: wantsHotel ? budgetTier : null,
    });
    if (res.status !== "ok") {
      setError(res.message || "Booking failed.");
      return;
    }
    setResult(res);
    onBooked();
    try {
      const p = await api<Plan>(`/api/my-plan?code=${res.code}`);
      setPlan(p);
    } catch {
      // Plan is a bonus on top of a successful booking — a failed fetch here
      // shouldn't block the confirmation screen the attendee is waiting on.
    }
  }

  const total = tier.price * (seatId ? 1 : qty);

  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onClose}>
      <View style={styles.sheetOverlay}>
        <View style={styles.sheet}>
          {result ? (
            <View style={{ alignItems: "center" }}>
              <Text style={{ fontSize: 40 }}>✅</Text>
              <Text style={styles.confirmTitle}>Registration Confirmed</Text>
              <Text style={styles.mutedText}>{eventName}</Text>
              <View style={styles.qrWrap}>
                <QRCode value={result.code} size={140} color={colors.ink} backgroundColor="#fff" />
              </View>
              <Text style={styles.confirmCode}>{result.code}</Text>
              <Text style={styles.mutedText}>
                {result.tier}
                {result.seat_label ? ` · Seat ${result.seat_label}` : ""}
                {result.quantity > 1 ? ` · Qty ${result.quantity}` : ""} · ₹{result.total_price.toLocaleString()}
              </Text>
              {plan && <PlanSection plan={plan} />}
              <Pressable
                style={styles.confirmBtn}
                onPress={() => {
                  onClose();
                  router.push("/(tabs)/my-events");
                }}
              >
                <Text style={styles.registerBtnText}>GO TO MY EVENTS</Text>
              </Pressable>
            </View>
          ) : showSeats ? (
            <>
              <View style={styles.sheetHeader}>
                <Text style={styles.sheetTitle}>Pick a seat — {tier.name}</Text>
                <Pressable onPress={() => setShowSeats(false)}>
                  <Text style={styles.sheetClose}>✕</Text>
                </Pressable>
              </View>
              <View style={styles.seatGrid}>
                {seats.map((s) => (
                  <Pressable
                    key={s.id}
                    disabled={s.status === "booked"}
                    onPress={() => {
                      setSeatId(s.id);
                      setShowSeats(false);
                    }}
                    style={[styles.seat, s.status === "booked" && styles.seatBooked, seatId === s.id && styles.seatSelected]}
                  >
                    <Text style={styles.seatText}>{s.seat_label}</Text>
                  </Pressable>
                ))}
              </View>
            </>
          ) : (
            <>
              <View style={styles.sheetHeader}>
                <Text style={styles.sheetTitle}>Register for {eventName}</Text>
                <Pressable onPress={onClose}>
                  <Text style={styles.sheetClose}>✕</Text>
                </Pressable>
              </View>
              <Text style={styles.label}>Your name</Text>
              <TextInput style={styles.input} placeholder="Full name" placeholderTextColor={colors.muted} value={name} onChangeText={setName} />

              <View style={styles.tierSummary}>
                <View style={styles.row}>
                  <View>
                    <Text style={styles.tierName}>{tier.name}</Text>
                    <Text style={styles.tierMeta}>₹{tier.price.toLocaleString()} each</Text>
                  </View>
                  {tier.uses_seats && (
                    <Pressable style={styles.pickSeatBtn} onPress={openSeatPicker}>
                      <Text style={styles.pickSeatText}>{seatId ? "Change seat" : "Pick a seat"}</Text>
                    </Pressable>
                  )}
                </View>
                {!seatId && (
                  <View style={styles.qtyRow}>
                    <Text style={styles.mutedText}>Quantity</Text>
                    <View style={styles.stepper}>
                      <Pressable style={styles.stepperBtn} onPress={() => setQty((q) => Math.max(1, q - 1))}>
                        <Text style={styles.stepperText}>−</Text>
                      </Pressable>
                      <Text style={styles.qtyValue}>{qty}</Text>
                      <Pressable style={styles.stepperBtn} onPress={() => setQty((q) => Math.min(10, q + 1))}>
                        <Text style={styles.stepperText}>+</Text>
                      </Pressable>
                    </View>
                  </View>
                )}
                {!!seatId && <Text style={[styles.mutedText, { color: colors.accent, marginTop: 8 }]}>Seat selected</Text>}
              </View>

              {showHotelOption && (
                <View style={styles.tabGroupWrap}>
                  <Text style={styles.label}>Hotel — {hotels[0].name}</Text>
                  <View style={styles.tabGroup}>
                    <Pressable style={[styles.tabOpt, !wantsHotel && styles.tabOptActive]} onPress={() => setWantsHotel(false)}>
                      <Text style={[styles.tabOptText, !wantsHotel && styles.tabOptTextActive]}>No hotel</Text>
                    </Pressable>
                    <Pressable style={[styles.tabOpt, wantsHotel && styles.tabOptActive]} onPress={() => setWantsHotel(true)}>
                      <Text style={[styles.tabOptText, wantsHotel && styles.tabOptTextActive]}>Add hotel</Text>
                    </Pressable>
                  </View>
                  {wantsHotel && (
                    <View style={[styles.tabGroup, { marginTop: 8 }]}>
                      {BUDGET_OPTIONS.map((b) => (
                        <Pressable
                          key={b.value}
                          style={[styles.tabOpt, budgetTier === b.value && styles.tabOptActive]}
                          onPress={() => setBudgetTier(b.value)}
                        >
                          <Text style={[styles.tabOptText, budgetTier === b.value && styles.tabOptTextActive]}>{b.label}</Text>
                        </Pressable>
                      ))}
                    </View>
                  )}
                </View>
              )}

              {hasTransport && (
                <View style={styles.tabGroupWrap}>
                  <Text style={styles.label}>Transport</Text>
                  <View style={styles.tabGroup}>
                    <Pressable style={[styles.tabOpt, !wantsTransport && styles.tabOptActive]} onPress={() => setWantsTransport(false)}>
                      <Text style={[styles.tabOptText, !wantsTransport && styles.tabOptTextActive]}>No transport</Text>
                    </Pressable>
                    <Pressable style={[styles.tabOpt, wantsTransport && styles.tabOptActive]} onPress={() => setWantsTransport(true)}>
                      <Text style={[styles.tabOptText, wantsTransport && styles.tabOptTextActive]}>Request transport</Text>
                    </Pressable>
                  </View>
                </View>
              )}

              <View style={styles.totalRow}>
                <Text style={styles.totalLabel}>Total</Text>
                <Text style={styles.totalValue}>₹{total.toLocaleString()}</Text>
              </View>

              <Pressable style={styles.confirmBtn} onPress={confirm}>
                <Text style={styles.registerBtnText}>CONFIRM REGISTRATION</Text>
              </Pressable>
              {!!error && <Text style={styles.errorText}>{error}</Text>}
            </>
          )}
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  loading: { flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: colors.bg },
  topBar: { paddingHorizontal: spacing.lg, paddingTop: spacing.lg, paddingBottom: spacing.sm },
  backText: { color: colors.muted, fontSize: 12 },
  banner: { height: 160, backgroundColor: colors.pastelBlue, alignItems: "center", justifyContent: "center" },
  card: {
    backgroundColor: colors.panel,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.md,
    marginHorizontal: spacing.lg,
    marginTop: spacing.md,
    ...shadow.card,
  },
  row: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 6 },
  eventName: { fontSize: 18, fontWeight: "800", color: colors.ink, flex: 1 },
  livePill: { backgroundColor: colors.ok, borderRadius: radius.pill, paddingVertical: 3, paddingHorizontal: 9 },
  liveText: { color: "#fff", fontSize: 9, fontWeight: "800" },
  datetime: { fontSize: 11, color: colors.muted, marginTop: 6 },
  venue: { fontSize: 13, fontWeight: "700", color: colors.ink, marginTop: 8 },
  address: { fontSize: 11, color: colors.muted },
  statRow: { flexDirection: "row", gap: spacing.lg, marginTop: 8 },
  stat: { fontSize: 11, color: colors.muted },
  sectionTitle: { fontSize: 11, fontWeight: "800", letterSpacing: 1, color: colors.ink, textTransform: "uppercase", marginBottom: 8 },
  bodyText: { fontSize: 13, color: colors.ink, lineHeight: 18 },
  mutedText: { fontSize: 12, color: colors.muted },
  listRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingVertical: 6, gap: 8 },
  rowLeft: { flexDirection: "row", alignItems: "center", gap: 8 },
  rowLabel: { fontSize: 13, color: colors.ink, fontWeight: "600" },
  rowValue: { fontSize: 11, color: colors.muted },
  dot: { width: 9, height: 9, borderRadius: 5 },
  tierCard: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    borderWidth: 1.5,
    borderColor: colors.border,
    borderRadius: radius.md,
    padding: spacing.md,
    marginBottom: spacing.sm,
  },
  tierCardActive: { borderColor: colors.accent, backgroundColor: colors.pastelBlue },
  tierCardDisabled: { opacity: 0.5 },
  tierName: { fontSize: 14, fontWeight: "800", color: colors.ink },
  tierMeta: { fontSize: 10.5, color: colors.muted, marginTop: 2 },
  tierPrice: { fontSize: 14, fontWeight: "800", color: colors.accent },
  bottomBar: {
    position: "absolute",
    bottom: 0,
    left: 0,
    right: 0,
    padding: spacing.md,
    backgroundColor: colors.bg,
    borderTopWidth: 1,
    borderTopColor: colors.border,
  },
  registerBtn: { backgroundColor: colors.accent, borderRadius: radius.lg, paddingVertical: 14, alignItems: "center" },
  registerBtnDisabled: { opacity: 0.5 },
  registerBtnText: { color: "#fff", fontWeight: "800", fontSize: 13 },
  sheetOverlay: { flex: 1, backgroundColor: "rgba(18,59,109,0.45)", justifyContent: "flex-end" },
  sheet: { backgroundColor: "#fff", borderTopLeftRadius: radius.xl, borderTopRightRadius: radius.xl, padding: spacing.lg, maxHeight: "88%" },
  sheetHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: spacing.md },
  sheetTitle: { fontSize: 15, fontWeight: "800", color: colors.ink, flex: 1 },
  sheetClose: { fontSize: 18, color: colors.muted },
  label: { fontSize: 10.5, fontWeight: "700", color: colors.muted, textTransform: "uppercase", letterSpacing: 1, marginBottom: 4 },
  input: { borderWidth: 1, borderColor: colors.border, borderRadius: radius.md, padding: 12, color: colors.ink, marginBottom: spacing.md },
  tierSummary: { backgroundColor: colors.bg, borderRadius: radius.md, padding: spacing.md, marginBottom: spacing.md },
  pickSeatBtn: { backgroundColor: colors.pastelBlue, borderRadius: radius.md, paddingVertical: 8, paddingHorizontal: 12 },
  pickSeatText: { fontSize: 11, fontWeight: "700", color: colors.ink },
  qtyRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginTop: spacing.md },
  stepper: { flexDirection: "row", alignItems: "center", gap: 12 },
  stepperBtn: { width: 32, height: 32, borderRadius: 8, backgroundColor: colors.panel, borderWidth: 1, borderColor: colors.border, alignItems: "center", justifyContent: "center" },
  stepperText: { fontSize: 16, fontWeight: "800", color: colors.ink },
  qtyValue: { fontSize: 14, fontWeight: "800", color: colors.ink, width: 20, textAlign: "center" },
  tabGroupWrap: { marginBottom: spacing.md },
  tabGroup: { flexDirection: "row", gap: 6, backgroundColor: colors.bg, borderRadius: radius.md, padding: 4 },
  tabOpt: { flex: 1, alignItems: "center", paddingVertical: 8, borderRadius: 8 },
  tabOptActive: { backgroundColor: colors.accent },
  tabOptText: { fontSize: 11.5, fontWeight: "700", color: colors.muted },
  tabOptTextActive: { color: "#fff" },
  totalRow: { flexDirection: "row", justifyContent: "space-between", marginVertical: spacing.md },
  totalLabel: { fontWeight: "800", color: colors.ink },
  totalValue: { fontWeight: "800", color: colors.accent, fontFamily: "monospace" },
  confirmBtn: { backgroundColor: colors.accent, borderRadius: radius.lg, paddingVertical: 14, alignItems: "center", width: "100%", marginTop: spacing.sm },
  errorText: { color: colors.warn, fontSize: 12, marginTop: spacing.sm, textAlign: "center" },
  confirmTitle: { fontSize: 17, fontWeight: "800", color: colors.ink, marginTop: spacing.sm },
  qrWrap: { backgroundColor: "#fff", padding: spacing.md, borderRadius: radius.lg, marginVertical: spacing.md },
  confirmCode: { fontSize: 22, letterSpacing: 5, fontWeight: "800", color: colors.accent, marginBottom: spacing.sm },
  planBox: { width: "100%", backgroundColor: colors.bg, borderRadius: radius.lg, padding: spacing.md, marginTop: spacing.md },
  planTitle: { fontSize: 11, fontWeight: "800", letterSpacing: 1, color: colors.ink, textTransform: "uppercase", marginBottom: spacing.sm },
  planRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 8, paddingVertical: 6 },
  planRowStack: { paddingVertical: 6 },
  planLabel: { fontSize: 10, fontWeight: "700", color: colors.muted, textTransform: "uppercase", letterSpacing: 0.5 },
  planValue: { fontSize: 13, fontWeight: "700", color: colors.ink, marginTop: 2 },
  mapBtn: { backgroundColor: colors.accent, borderRadius: radius.md, paddingVertical: 8, paddingHorizontal: 10 },
  mapBtnText: { color: "#fff", fontSize: 10.5, fontWeight: "700" },
  seatGrid: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  seat: { width: "15%", aspectRatio: 1, borderRadius: 6, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.bg, alignItems: "center", justifyContent: "center" },
  seatBooked: { backgroundColor: colors.high, opacity: 0.6 },
  seatSelected: { backgroundColor: colors.accent },
  seatText: { fontSize: 9, color: colors.ink, fontFamily: "monospace" },
});
