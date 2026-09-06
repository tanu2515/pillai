import { useEffect, useState } from "react";
import { View, Text, TextInput, Pressable, StyleSheet, ActivityIndicator, Image, ScrollView, Alert } from "react-native";
import { router } from "expo-router";
import { apiPost, setEmail as saveEmail, getApiBase, setApiBase } from "../src/api";
import { colors, radius, spacing } from "../src/theme";

const ROLES = ["Attendee", "Event Command Operator"];

export default function Login() {
  const [email, setEmailInput] = useState("");
  const [role, setRole] = useState("Attendee");
  const [status, setStatus] = useState("");
  const [loading, setLoading] = useState(false);
  const [showServer, setShowServer] = useState(false);
  const [apiBase, setApiBaseInput] = useState("");

  useEffect(() => {
    (async () => setApiBaseInput(await getApiBase()))();
  }, []);

  async function saveServer() {
    await setApiBase(apiBase.trim());
    Alert.alert("Server updated", `Now pointing at ${apiBase.trim()}`);
  }

  async function doLogin() {
    if (!email.trim()) {
      setStatus("Enter an email address to continue.");
      return;
    }
    setLoading(true);
    setStatus("Signing in…");
    try {
      const data = await apiPost<{ email: string; role: string; status: string }>("/api/auth/login", {
        email: email.trim(),
        role,
      });
      await saveEmail(data.email);
      setStatus(
        data.status === "signup" ? `Welcome — new ${data.role} account created.` : `Welcome back, ${data.email}.`
      );
      setTimeout(() => {
        router.replace(role === "Attendee" ? "/(tabs)/home" : "/live-status");
      }, 400);
    } catch (e) {
      setStatus("Couldn't reach the server — check the API address in Profile once signed in, or your connection.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <Image source={require("../assets/vyavastha-icon.png")} style={styles.logo} />
      <Text style={styles.brand}>VYAVASTHA</Text>
      <View style={styles.card}>
        <Text style={styles.title}>Sign In</Text>
        <Text style={styles.label}>Email ID</Text>
        <TextInput
          style={styles.input}
          placeholder="you@example.com"
          placeholderTextColor={colors.muted}
          autoCapitalize="none"
          keyboardType="email-address"
          value={email}
          onChangeText={setEmailInput}
        />
        <Text style={styles.label}>Role</Text>
        <View style={styles.roleWrap}>
          {ROLES.map((r) => (
            <Pressable key={r} onPress={() => setRole(r)} style={[styles.rolePill, role === r && styles.rolePillActive]}>
              <Text style={[styles.roleText, role === r && styles.roleTextActive]}>{r}</Text>
            </Pressable>
          ))}
        </View>
        <Pressable style={styles.btn} onPress={doLogin} disabled={loading}>
          {loading ? <ActivityIndicator color="#fff" /> : <Text style={styles.btnText}>SIGN IN</Text>}
        </Pressable>
        {!!status && <Text style={styles.status}>{status}</Text>}
        <Text style={styles.demoNote}>Demo login only — matches your email + role against existing records.</Text>

        <Pressable onPress={() => setShowServer((s) => !s)}>
          <Text style={styles.serverToggle}>{showServer ? "Hide server settings" : "⚙ Server settings"}</Text>
        </Pressable>
        {showServer && (
          <View style={styles.serverBox}>
            <Text style={styles.label}>API server address</Text>
            <Text style={styles.hint}>
              On a physical device this must be your computer's LAN IP (e.g. http://192.168.1.5:8001), not localhost.
            </Text>
            <TextInput style={styles.input} autoCapitalize="none" value={apiBase} onChangeText={setApiBaseInput} />
            <Pressable style={styles.serverSaveBtn} onPress={saveServer}>
              <Text style={styles.btnText}>SAVE SERVER ADDRESS</Text>
            </Pressable>
          </View>
        )}
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flexGrow: 1, backgroundColor: colors.bg, alignItems: "center", justifyContent: "center", padding: spacing.lg },
  logo: { width: 56, height: 56, borderRadius: radius.md, marginBottom: spacing.sm },
  brand: { fontSize: 20, fontWeight: "900", color: colors.ink, marginBottom: spacing.lg, letterSpacing: 1 },
  card: {
    width: "100%",
    maxWidth: 380,
    backgroundColor: colors.panel,
    borderRadius: radius.xl,
    padding: spacing.lg,
    borderWidth: 1,
    borderColor: colors.border,
  },
  title: { fontSize: 18, fontWeight: "800", color: colors.ink, marginBottom: spacing.md },
  label: {
    fontSize: 11,
    fontWeight: "700",
    color: colors.muted,
    textTransform: "uppercase",
    letterSpacing: 1,
    marginBottom: 4,
    marginTop: spacing.sm,
  },
  input: { borderWidth: 1, borderColor: colors.border, borderRadius: radius.md, padding: 12, color: colors.ink, fontSize: 14 },
  roleWrap: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  rolePill: { paddingVertical: 6, paddingHorizontal: 12, borderRadius: radius.pill, backgroundColor: colors.pastelBlue },
  rolePillActive: { backgroundColor: colors.accent },
  roleText: { fontSize: 11, fontWeight: "700", color: colors.ink },
  roleTextActive: { color: "#fff" },
  btn: { backgroundColor: colors.accent, borderRadius: radius.lg, paddingVertical: 14, alignItems: "center", marginTop: spacing.lg },
  btnText: { color: "#fff", fontWeight: "800", letterSpacing: 1 },
  status: { color: colors.muted, fontSize: 12, marginTop: spacing.sm, textAlign: "center" },
  demoNote: { color: colors.muted, fontSize: 10, marginTop: spacing.md, textAlign: "center", lineHeight: 14 },
  serverToggle: { color: colors.muted, fontSize: 11, marginTop: spacing.md, textAlign: "center", textDecorationLine: "underline" },
  serverBox: { marginTop: spacing.sm },
  hint: { fontSize: 10.5, color: colors.muted, marginBottom: 8, lineHeight: 14 },
  serverSaveBtn: { backgroundColor: colors.accent, borderRadius: radius.md, paddingVertical: 10, alignItems: "center", marginTop: spacing.sm },
});
