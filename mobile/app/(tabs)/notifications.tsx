import { useCallback, useEffect } from "react";
import { View, Text, SectionList, Pressable, StyleSheet } from "react-native";
import { useFocusEffect } from "expo-router";
import { useState } from "react";
import { Header } from "../../src/components/Header";
import { api, apiPost } from "../../src/api";
import { colors, radius, spacing, shadow } from "../../src/theme";

type Notif = {
  id: number;
  title: string;
  message: string;
  priority: "CRITICAL" | "HIGH" | "MEDIUM" | "LOW";
  is_read: boolean;
  event_name?: string | null;
  created_at?: string;
};

// Groups by severity tier (Critical/Important/Information) rather than a
// flat list — the underlying priority field is unchanged, only the grouping
// shown to attendees is added on top.
const GROUPS: { key: string; title: string; priorities: Notif["priority"][] }[] = [
  { key: "CRITICAL", title: "🔴 Critical", priorities: ["CRITICAL"] },
  { key: "HIGH", title: "🟠 Important", priorities: ["HIGH"] },
  { key: "INFO", title: "🟢 Information", priorities: ["MEDIUM", "LOW"] },
];

const SEVERITY: Record<string, { color: string; icon: string }> = {
  CRITICAL: { color: colors.danger, icon: "🔴" },
  HIGH: { color: colors.high, icon: "🟠" },
  MEDIUM: { color: colors.warn, icon: "🟡" },
  LOW: { color: colors.ok, icon: "🟢" },
};

function timeAgo(iso?: string) {
  if (!iso) return "";
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return new Date(iso).toDateString();
}

export default function Notifications() {
  const [list, setList] = useState<Notif[]>([]);

  const load = useCallback(async () => {
    const data = await api<Notif[]>("/api/notifications?role=Attendee");
    setList(data);
  }, []);

  useFocusEffect(
    useCallback(() => {
      load();
      const interval = setInterval(load, 8000);
      return () => clearInterval(interval);
    }, [load])
  );

  async function markRead(id: number) {
    await apiPost(`/api/notifications/${id}/read`, {});
    load();
  }

  const sections = GROUPS.map((g) => ({
    key: g.key,
    title: g.title,
    data: list.filter((n) => g.priorities.includes(n.priority)),
  })).filter((s) => s.data.length > 0);

  return (
    <View style={styles.container}>
      <Header title="Alerts" subtitle="Shared alerts for attendees at this event — not a private per-user inbox." />
      <SectionList
        sections={sections}
        keyExtractor={(n) => String(n.id)}
        contentContainerStyle={{ padding: spacing.lg, gap: spacing.sm }}
        stickySectionHeadersEnabled={false}
        ListEmptyComponent={
          <Text style={styles.empty}>No alerts yet — you'll see gate, transport, and safety alerts here once an event is live.</Text>
        }
        renderSectionHeader={({ section }) => (
          <Text style={styles.sectionHeader}>{section.title} ({section.data.length})</Text>
        )}
        renderItem={({ item }) => {
          const sev = SEVERITY[item.priority] || SEVERITY.MEDIUM;
          return (
            <Pressable
              style={[styles.card, !item.is_read && { borderLeftWidth: 3, borderLeftColor: colors.accent }]}
              onPress={() => markRead(item.id)}
            >
              <Text style={{ color: sev.color, fontSize: 16 }}>{sev.icon}</Text>
              <View style={{ flex: 1 }}>
                <Text style={styles.title}>{item.title}</Text>
                {!!item.event_name && <Text style={styles.eventName}>{item.event_name}</Text>}
                <Text style={styles.message}>{item.message}</Text>
                <Text style={styles.time}>{timeAgo(item.created_at)}</Text>
              </View>
            </Pressable>
          );
        }}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  card: {
    flexDirection: "row",
    gap: 10,
    backgroundColor: colors.panel,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.md,
    ...shadow.card,
  },
  sectionHeader: { fontSize: 11, fontWeight: "800", letterSpacing: 1, color: colors.ink, textTransform: "uppercase", marginBottom: spacing.sm, marginTop: spacing.sm },
  title: { fontWeight: "800", fontSize: 13, color: colors.ink },
  eventName: { fontSize: 10.5, color: colors.muted, fontFamily: "monospace", marginTop: 1 },
  message: { fontSize: 12.5, color: colors.ink, marginTop: 2 },
  time: { fontSize: 10, color: colors.muted, marginTop: 4 },
  empty: { color: colors.muted, fontSize: 12, textAlign: "center", marginTop: 40 },
});
