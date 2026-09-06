import { useEffect, useRef } from "react";
import { View, Text, Image, StyleSheet, Animated, Pressable } from "react-native";
import { router } from "expo-router";
import { getEmail } from "../src/api";
import { colors } from "../src/theme";

export default function Splash() {
  const scale = useRef(new Animated.Value(0.9)).current;
  const opacity = useRef(new Animated.Value(0)).current;
  const navigated = useRef(false);

  useEffect(() => {
    Animated.parallel([
      Animated.timing(scale, { toValue: 1, duration: 700, useNativeDriver: true }),
      Animated.timing(opacity, { toValue: 1, duration: 700, useNativeDriver: true }),
    ]).start();
    const t = setTimeout(goNext, 2200);
    return () => clearTimeout(t);
  }, []);

  async function goNext() {
    if (navigated.current) return;
    navigated.current = true;
    const email = await getEmail();
    router.replace(email ? "/(tabs)/home" : "/login");
  }

  return (
    <Pressable style={styles.container} onPress={goNext}>
      <Animated.Image
        source={require("../assets/vyavastha-logo.png")}
        style={[styles.logo, { transform: [{ scale }], opacity }]}
        resizeMode="contain"
      />
      <Text style={styles.hint}>Tap to continue</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg, alignItems: "center", justifyContent: "center" },
  logo: { width: 220, height: 220 },
  hint: { position: "absolute", bottom: 40, color: colors.muted, fontSize: 12 },
});
