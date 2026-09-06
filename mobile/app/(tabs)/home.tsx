import { useCallback, useState } from "react";
import { View, Text, FlatList, StyleSheet, RefreshControl, Pressable } from "react-native";
import { useFocusEffect, router } from "expo-router";
import { Header } from "../../src/components/Header";
import { EventCard, EventSummary } from "../../src/components/EventCard";
import { api } from "../../src/api";
import { colors, spacing, radius } from "../../src/theme";
import { ChatFab } from "../../src/components/ChatFab";

type Sections = {
  upcoming: EventSummary[];
  popular: EventSummary[];
  near: EventSummary[];
  recommended: EventSummary[];
  live: EventSummary[];
};

const CARD_WIDTH = 190;

function Rail({ title, data, onSeeAll, emptyMsg }: { title: string; data: EventSummary[]; onSeeAll?: () => void; emptyMsg: string }) {
  return (
    <View style={styles.section}>
      <View style={styles.sectionHeader}>
        <Text style={styles.eyebrow}>{title}</Text>
        {!!onSeeAll && (
          <Pressable onPress={onSeeAll}>
            <Text style={styles.seeAll}>See all →</Text>
          </Pressable>
        )}
      </View>
      {data.length ? (
        <FlatList
          horizontal
          showsHorizontalScrollIndicator={false}
          data={data}
          keyExtractor={(e) => String(e.id)}
          contentContainerStyle={{ gap: spacing.md, paddingHorizontal: spacing.lg }}
          renderItem={({ item }) => <EventCard event={item} width={CARD_WIDTH} />}
        />
      ) : (
        <Text style={styles.empty}>{emptyMsg}</Text>
      )}
    </View>
  );
}

export default function Home() {
  const [sections, setSections] = useState<Sections>({ upcoming: [], popular: [], near: [], recommended: [], live: [] });
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    const [upcoming, popular, near, recommended, all] = await Promise.all([
      api<EventSummary[]>("/api/events?section=upcoming"),
      api<EventSummary[]>("/api/events?section=popular"),
      api<EventSummary[]>("/api/events?section=near_you"),
      api<EventSummary[]>("/api/events?section=recommended"),
      api<EventSummary[]>("/api/events"),
    ]);
    setSections({
      upcoming: upcoming.slice(0, 8),
      popular,
      near: near.filter((e) => e.status !== "live"),
      recommended: recommended.slice().reverse(),
      live: all.filter((e) => e.status === "live"),
    });
  }, []);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load])
  );

  async function onRefresh() {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  }

  return (
    <View style={styles.container}>
      <FlatList
        data={[1]}
        keyExtractor={() => "home"}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.accent} />}
        ListHeaderComponent={
          <>
            <Header title="VYAVASTHA" subtitle="Discover events near you" />
            <Pressable style={styles.searchBar} onPress={() => router.push("/(tabs)/explore")}>
              <Text style={styles.searchIcon}>🔍</Text>
              <Text style={styles.searchPlaceholder}>Search events, venues, cities...</Text>
            </Pressable>
          </>
        }
        renderItem={() => (
          <>
            <Rail title="UPCOMING EVENTS" data={sections.upcoming} emptyMsg="No upcoming events right now." onSeeAll={() => router.push("/(tabs)/explore")} />
            <Rail title="POPULAR EVENTS" data={sections.popular} emptyMsg="No popular events yet." />
            <Rail title="EVENTS NEAR YOU" data={sections.near} emptyMsg="No events near you yet." />
            <Rail title="RECOMMENDED FOR YOU" data={sections.recommended} emptyMsg="Nothing recommended yet." />
            <Rail title="LIVE NOW" data={sections.live} emptyMsg="No event is live right now." onSeeAll={() => router.push("/live-status")} />
          </>
        )}
        contentContainerStyle={{ paddingBottom: 40 }}
      />
      <ChatFab />
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  searchBar: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    marginHorizontal: spacing.lg,
    marginTop: spacing.sm,
    backgroundColor: colors.panel,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.lg,
    paddingVertical: 12,
    paddingHorizontal: 14,
  },
  searchIcon: { fontSize: 14 },
  searchPlaceholder: { color: colors.muted, fontSize: 13 },
  section: { marginTop: spacing.lg },
  sectionHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingHorizontal: spacing.lg,
    marginBottom: spacing.sm,
  },
  eyebrow: { fontSize: 11, fontWeight: "800", letterSpacing: 1, color: colors.ink, textTransform: "uppercase" },
  seeAll: { fontSize: 10.5, fontWeight: "800", color: colors.accent, textTransform: "uppercase" },
  empty: { color: colors.muted, fontSize: 12, paddingHorizontal: spacing.lg },
});
