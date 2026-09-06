import { useCallback, useEffect } from "react";
import { View, Text, FlatList, Pressable, StyleSheet } from "react-native";
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
  created_at?: string;
};

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

  return (
    <View style={styles.container}>
      <Header title="Notifications" subtitle="Gate, transport, and safety alerts for your events." />
      <FlatList
        data={list}
        keyExtractor={(n) => String(n.id)}
        contentContainerStyle={{ padding: spacing.lg, gap: spacing.sm }}
        ListEmptyComponent={
          <Text style={styles.empty}>No notifications yet — you'll see gate, transport, and safety alerts here once an event is live.</Text>
        }
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
  title: { fontWeight: "800", fontSize: 13, color: colors.ink },
  message: { fontSize: 12.5, color: colors.ink, marginTop: 2 },
  time: { fontSize: 10, color: colors.muted, marginTop: 4 },
  empty: { color: colors.muted, fontSize: 12, textAlign: "center", marginTop: 40 },
});
