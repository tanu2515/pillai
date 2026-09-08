import { useEffect, useState } from "react";
import { View, Text, TextInput, Pressable, StyleSheet, ScrollView, Alert } from "react-native";
import { router } from "expo-router";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { Header } from "../../src/components/Header";
import { getEmail, clearSession, getApiBase, setApiBase } from "../../src/api";
import { colors, radius, spacing, shadow } from "../../src/theme";

export default function Profile() {
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [apiBase, setApiBaseInput] = useState("");
  const [saveStatus, setSaveStatus] = useState("");
  const [accessiblePref, setAccessiblePref] = useState(false);
  const [showPrefs, setShowPrefs] = useState(false);
  const [showHelp, setShowHelp] = useState(false);
  const [showDevOptions, setShowDevOptions] = useState(false);

  useEffect(() => {
    (async () => {
      setEmail(await getEmail());
      setName((await AsyncStorage.getItem("vyavastha_profile_name")) || "");
      setPhone((await AsyncStorage.getItem("vyavastha_profile_phone")) || "");
      setApiBaseInput(await getApiBase());
      setAccessiblePref((await AsyncStorage.getItem("vyavastha_pref_accessible")) === "1");
    })();
  }, []);

  async function saveProfile() {
    await AsyncStorage.setItem("vyavastha_profile_name", name);
    await AsyncStorage.setItem("vyavastha_profile_phone", phone);
    setSaveStatus("Saved.");
    setTimeout(() => setSaveStatus(""), 1500);
  }

  async function toggleAccessiblePref() {
    const next = !accessiblePref;
    setAccessiblePref(next);
    await AsyncStorage.setItem("vyavastha_pref_accessible", next ? "1" : "0");
  }

  async function saveServer() {
    await setApiBase(apiBase.trim());
    Alert.alert("Server updated", `Now pointing at ${apiBase.trim()}`);
  }

  async function logout() {
    await clearSession();
    router.replace("/login");
  }

  return (
    <ScrollView style={styles.container} contentContainerStyle={{ padding: spacing.lg, paddingBottom: 60 }}>
      <Header title="Profile" />

      <View style={styles.card}>
        <View style={styles.avatar}>
          <Text style={styles.avatarText}>{(email || "?").charAt(0).toUpperCase()}</Text>
        </View>
        <View style={{ flex: 1 }}>
          <TextInput style={styles.nameInput} placeholder="Your name" placeholderTextColor={colors.muted} value={name} onChangeText={setName} />
          <Text style={styles.email}>{email || "Not signed in"}</Text>
        </View>
      </View>

      <View style={styles.card}>
        <Text style={styles.label}>Phone number</Text>
        <TextInput
          style={styles.input}
          placeholder="+91 XXXXX XXXXX"
          placeholderTextColor={colors.muted}
          keyboardType="phone-pad"
          value={phone}
          onChangeText={setPhone}
        />
        <Pressable style={styles.saveBtn} onPress={saveProfile}>
          <Text style={styles.saveBtnText}>SAVE</Text>
        </Pressable>
        {!!saveStatus && <Text style={styles.savedText}>{saveStatus}</Text>}
      </View>

      <View style={styles.menuCard}>
        <Pressable style={styles.menuItem} onPress={() => router.push("/(tabs)/my-events")}>
          <Text style={styles.menuText}>🎟️ My Event &amp; Registrations</Text>
          <Text style={styles.chevron}>›</Text>
        </Pressable>
        <Pressable style={styles.menuItem} onPress={() => router.push("/(tabs)/notifications")}>
          <Text style={styles.menuText}>🔔 Notifications</Text>
          <Text style={styles.chevron}>›</Text>
        </Pressable>
        <Pressable style={styles.menuItem} onPress={() => setShowPrefs((v) => !v)}>
          <Text style={styles.menuText}>⚙️ Preferences</Text>
          <Text style={styles.chevron}>›</Text>
        </Pressable>
        {showPrefs && (
          <View style={styles.subPanel}>
            <Pressable style={styles.rowBetween} onPress={toggleAccessiblePref}>
              <Text style={[styles.menuText, { flex: 1, fontSize: 12.5 }]}>Always request a wheelchair-accessible exit for my bookings</Text>
              <Text style={{ fontWeight: "800", color: accessiblePref ? colors.accent : colors.muted }}>{accessiblePref ? "ON" : "OFF"}</Text>
            </Pressable>
            <Text style={styles.hint}>Used as the default in My Event's accessibility toggle — you can still change it per event.</Text>
          </View>
        )}
        <Pressable style={[styles.menuItem, { borderBottomWidth: 0 }]} onPress={() => setShowHelp((v) => !v)}>
          <Text style={styles.menuText}>❓ Help</Text>
          <Text style={styles.chevron}>›</Text>
        </Pressable>
        {showHelp && (
          <View style={styles.subPanel}>
            <Text style={styles.hint}>
              Contact your event organizer or use the AI chatbot on the event page for help. For urgent safety issues, use the accessibility/evacuation section in My Event or speak to venue staff directly.
            </Text>
          </View>
        )}
      </View>

      <Pressable style={styles.logoutBtn} onPress={logout}>
        <Text style={styles.logoutText}>LOG OUT</Text>
      </Pressable>

      <Pressable style={styles.devToggle} onPress={() => setShowDevOptions((v) => !v)}>
        <Text style={styles.devToggleText}>{showDevOptions ? "Hide" : "Show"} developer options</Text>
      </Pressable>
      {showDevOptions && (
        <View style={styles.card}>
          <Text style={styles.label}>API server address</Text>
          <Text style={styles.hint}>
            On a physical device this must be your computer's LAN IP (e.g. http://192.168.1.5:8001), not localhost.
          </Text>
          <TextInput style={styles.input} autoCapitalize="none" value={apiBase} onChangeText={setApiBaseInput} />
          <Pressable style={styles.saveBtn} onPress={saveServer}>
            <Text style={styles.saveBtnText}>UPDATE SERVER</Text>
          </Pressable>
        </View>
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  card: {
    backgroundColor: colors.panel,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.md,
    marginBottom: spacing.md,
    ...shadow.card,
  },
  avatarRow: { flexDirection: "row" },
  avatar: {
    width: 56,
    height: 56,
    borderRadius: 28,
    backgroundColor: colors.accent,
    alignItems: "center",
    justifyContent: "center",
    marginRight: spacing.md,
  },
  avatarText: { color: "#fff", fontWeight: "800", fontSize: 22 },
  nameInput: { fontSize: 14, fontWeight: "700", color: colors.ink, paddingVertical: 4 },
  email: { fontSize: 11, color: colors.muted },
  label: { fontSize: 10, fontWeight: "700", color: colors.muted, textTransform: "uppercase", letterSpacing: 1, marginBottom: 6 },
  hint: { fontSize: 10.5, color: colors.muted, marginBottom: 8, lineHeight: 14 },
  input: { borderWidth: 1, borderColor: colors.border, borderRadius: radius.md, padding: 10, color: colors.ink, fontSize: 13 },
  saveBtn: { backgroundColor: colors.accent, borderRadius: radius.md, paddingVertical: 10, alignItems: "center", marginTop: spacing.sm },
  saveBtnText: { color: "#fff", fontWeight: "800", fontSize: 11 },
  savedText: { color: colors.ok, fontSize: 11, marginTop: 6, textAlign: "center" },
  menuCard: {
    backgroundColor: colors.panel,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: colors.border,
    marginBottom: spacing.md,
    overflow: "hidden",
  },
  menuItem: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    padding: 14,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  menuText: { fontSize: 13, color: colors.ink },
  chevron: { color: colors.muted, fontSize: 16 },
  subPanel: { padding: 14, backgroundColor: colors.bg, borderBottomWidth: 1, borderBottomColor: colors.border },
  rowBetween: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", gap: 8 },
  logoutBtn: { backgroundColor: colors.panel, borderWidth: 1, borderColor: colors.high, borderRadius: radius.lg, paddingVertical: 14, alignItems: "center", marginBottom: spacing.lg },
  logoutText: { color: colors.danger, fontWeight: "800", fontSize: 13 },
  devToggle: { alignItems: "center", paddingVertical: spacing.sm },
  devToggleText: { color: colors.muted, fontSize: 11, textDecorationLine: "underline" },
});
