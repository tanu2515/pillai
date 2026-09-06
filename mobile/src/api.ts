import { Platform } from "react-native";
import AsyncStorage from "@react-native-async-storage/async-storage";

// "localhost" means something different on every target: a browser tab, an
// Android emulator (needs 10.0.2.2 to reach the host machine), and a real
// phone (needs the host machine's LAN IP) all resolve it differently — so
// this is overridable at runtime (see setApiBase) rather than hardcoded,
// and a real device MUST set it via the Profile screen before anything works.
const DEFAULT_HOST = Platform.select({
  android: "http://10.0.2.2:8001",
  default: "http://localhost:8001",
});

let cachedBase: string | null = null;

export async function getApiBase(): Promise<string> {
  if (cachedBase) return cachedBase;
  const stored = await AsyncStorage.getItem("vyavastha_api_base");
  cachedBase = stored || DEFAULT_HOST || "http://localhost:8001";
  return cachedBase;
}

export async function setApiBase(url: string): Promise<void> {
  cachedBase = url;
  await AsyncStorage.setItem("vyavastha_api_base", url);
}

export async function api<T = any>(path: string, opts?: RequestInit): Promise<T> {
  const base = await getApiBase();
  const res = await fetch(base + path, opts);
  return res.json();
}

export async function apiPost<T = any>(path: string, body: object): Promise<T> {
  return api<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function apiPatch<T = any>(path: string, body: object): Promise<T> {
  return api<T>(path, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function apiDelete<T = any>(path: string): Promise<T> {
  return api<T>(path, { method: "DELETE" });
}

// --- session (mirrors sessionStorage on web, but persistent via AsyncStorage
// since a mobile app doesn't have a natural "close the tab" reset point) ----

export async function getEmail(): Promise<string> {
  return (await AsyncStorage.getItem("vyavastha_email")) || "";
}

export async function setEmail(email: string): Promise<void> {
  await AsyncStorage.setItem("vyavastha_email", email);
}

export async function clearSession(): Promise<void> {
  await AsyncStorage.multiRemove(["vyavastha_email", "vyavastha_role", "vyavastha_my_gate"]);
}
