import { View, Text, Image, StyleSheet } from "react-native";
import { colors, spacing } from "../theme";

export function Header({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <View style={styles.wrap}>
      <View style={styles.row}>
        <Image source={require("../../assets/vyavastha-icon.png")} style={styles.logo} />
        <Text style={styles.title}>{title}</Text>
      </View>
      {!!subtitle && <Text style={styles.subtitle}>{subtitle}</Text>}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { paddingHorizontal: spacing.lg, paddingTop: spacing.lg, paddingBottom: spacing.sm },
  row: { flexDirection: "row", alignItems: "center", gap: 8 },
  logo: { width: 32, height: 32, borderRadius: 8 },
  title: { fontSize: 18, fontWeight: "900", color: colors.ink },
  subtitle: { fontSize: 12, color: colors.muted, marginTop: 4 },
});
