import { useEffect, useState, useCallback } from "react";
import { View, Text, TextInput, FlatList, Pressable, StyleSheet } from "react-native";
import { Header } from "../../src/components/Header";
import { EventCard, EventSummary } from "../../src/components/EventCard";
import { api } from "../../src/api";
import { colors, spacing, radius } from "../../src/theme";

export default function Explore() {
  const [categories, setCategories] = useState<string[]>([]);
  const [activeCategory, setActiveCategory] = useState("");
  const [search, setSearch] = useState("");
  const [events, setEvents] = useState<EventSummary[]>([]);

  useEffect(() => {
    api<string[]>("/api/event-categories").then(setCategories);
  }, []);

  const load = useCallback(async (q: string, cat: string) => {
    const params = new URLSearchParams();
    if (q) params.set("search", q);
    if (cat) params.set("category", cat);
    const data = await api<EventSummary[]>(`/api/events?${params.toString()}`);
    setEvents(data);
  }, []);

  useEffect(() => {
    const t = setTimeout(() => load(search, activeCategory), 300);
    return () => clearTimeout(t);
  }, [search, activeCategory, load]);

  return (
    <View style={styles.container}>
      <Header title="Explore" subtitle="Search across every event" />
      <View style={styles.searchWrap}>
        <Text style={styles.searchIcon}>🔍</Text>
        <TextInput
          style={styles.searchInput}
          placeholder="Search events, venues, cities..."
          placeholderTextColor={colors.muted}
          value={search}
          onChangeText={setSearch}
        />
      </View>
      <FlatList
        horizontal
        showsHorizontalScrollIndicator={false}
        data={["All", ...categories]}
        keyExtractor={(c) => c}
        contentContainerStyle={{ gap: 8, paddingHorizontal: spacing.lg, paddingVertical: spacing.sm }}
        renderItem={({ item }) => {
          const value = item === "All" ? "" : item;
          const active = value === activeCategory;
          return (
            <Pressable style={[styles.chip, active && styles.chipActive]} onPress={() => setActiveCategory(value)}>
              <Text style={[styles.chipText, active && styles.chipTextActive]}>{item}</Text>
            </Pressable>
          );
        }}
      />
      <FlatList
        data={events}
        keyExtractor={(e) => String(e.id)}
        numColumns={2}
        columnWrapperStyle={{ gap: spacing.md, paddingHorizontal: spacing.lg }}
        contentContainerStyle={{ gap: spacing.md, paddingBottom: 40, paddingTop: spacing.sm }}
        ListEmptyComponent={<Text style={styles.empty}>No events found{search ? ` for "${search}"` : ""}.</Text>}
        renderItem={({ item }) => <EventCard event={item} />}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  searchWrap: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    marginHorizontal: spacing.lg,
    marginTop: spacing.xs,
    backgroundColor: colors.panel,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.lg,
    paddingVertical: 10,
    paddingHorizontal: 14,
  },
  searchIcon: { fontSize: 14 },
  searchInput: { flex: 1, color: colors.ink, fontSize: 13 },
  chip: { paddingVertical: 7, paddingHorizontal: 14, borderRadius: radius.pill, backgroundColor: colors.panel, borderWidth: 1, borderColor: colors.border },
  chipActive: { backgroundColor: colors.accentDim, borderColor: colors.accentDim },
  chipText: { fontSize: 11.5, fontWeight: "700", color: colors.ink },
  chipTextActive: { color: "#fff" },
  empty: { color: colors.muted, fontSize: 12, textAlign: "center", marginTop: 40, paddingHorizontal: spacing.lg },
});
