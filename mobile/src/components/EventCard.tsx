import { View, Text, Pressable, StyleSheet } from "react-native";
import { router } from "expo-router";
import { colors, radius, spacing, shadow } from "../theme";

export type EventSummary = {
  id: number;
  name: string;
  description?: string;
  event_date?: string;
  event_time?: string;
  category?: string;
  city?: string;
  venue_name?: string;
  banner_emoji?: string;
  status: string;
  min_price?: number;
  max_price?: number;
  total_available: number;
  registration_status: string;
};

function fmtDate(e: EventSummary) {
  if (!e.event_date) return "Date TBA";
  const d = new Date(e.event_date);
  const s = d.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
  return e.event_time ? `${s} · ${e.event_time}` : s;
}
function fmtPrice(e: EventSummary) {
  if (e.min_price == null) return "";
  return e.min_price === e.max_price ? `₹${e.min_price.toLocaleString()}` : `From ₹${e.min_price.toLocaleString()}`;
}

export function EventCard({ event, width }: { event: EventSummary; width?: number }) {
  return (
    <Pressable style={[styles.card, width ? { width } : { flex: 1 }]} onPress={() => router.push(`/event/${event.id}`)}>
      <View style={styles.banner}>
        <Text style={{ fontSize: 34 }}>{event.banner_emoji || "🎉"}</Text>
      </View>
      <View style={styles.body}>
        <View style={styles.badgeRow}>
          {event.status === "live" && (
            <View style={styles.livePill}>
              <Text style={styles.liveText}>LIVE</Text>
            </View>
          )}
          {!!event.category && (
            <View style={styles.catPill}>
              <Text style={styles.catText}>{event.category}</Text>
            </View>
          )}
        </View>
        <Text style={styles.name} numberOfLines={2}>
          {event.name}
        </Text>
        <Text style={styles.meta}>{fmtDate(event)}</Text>
        <Text style={styles.meta} numberOfLines={1}>
          {[event.venue_name, event.city].filter(Boolean).join(" · ")}
        </Text>
        <View style={styles.footerRow}>
          <Text style={styles.price}>{fmtPrice(event)}</Text>
          <Text style={[styles.status, { color: event.registration_status === "Sold Out" ? colors.high : colors.ok }]}>
            {event.registration_status}
          </Text>
        </View>
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: colors.panel,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: colors.border,
    overflow: "hidden",
    ...shadow.card,
  },
  banner: { height: 90, backgroundColor: colors.pastelBlue, alignItems: "center", justifyContent: "center" },
  body: { padding: spacing.md },
  badgeRow: { flexDirection: "row", gap: 6, marginBottom: 6 },
  livePill: { backgroundColor: colors.ok, borderRadius: radius.pill, paddingVertical: 2, paddingHorizontal: 8 },
  liveText: { color: "#fff", fontSize: 9, fontWeight: "800" },
  catPill: { backgroundColor: colors.pastelBlue, borderRadius: radius.pill, paddingVertical: 2, paddingHorizontal: 8 },
  catText: { color: colors.accent, fontSize: 9, fontWeight: "700" },
  name: { fontSize: 13.5, fontWeight: "800", color: colors.ink, marginBottom: 4 },
  meta: { fontSize: 10.5, color: colors.muted },
  footerRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginTop: 8 },
  price: { color: colors.accent, fontWeight: "800", fontSize: 12 },
  status: { fontSize: 9.5, fontWeight: "800", textTransform: "uppercase" },
});
