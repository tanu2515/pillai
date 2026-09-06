import { useState, useRef } from "react";
import { View, Text, TextInput, Pressable, StyleSheet, Modal, FlatList, KeyboardAvoidingView, Platform } from "react-native";
import { apiPost } from "../api";
import { colors, radius, spacing } from "../theme";

type Msg = { id: string; who: "user" | "bot"; text: string; grounded?: string[] };

export function ChatFab() {
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<Msg[]>([
    { id: "welcome", who: "bot", text: 'Ask me about a gate, hotel, or transport — or try "where is my event", "which gate should I use".' },
  ]);
  const listRef = useRef<FlatList>(null);

  async function send() {
    const q = input.trim();
    if (!q) return;
    const userMsg: Msg = { id: Date.now() + "-u", who: "user", text: q };
    setMessages((m) => [...m, userMsg]);
    setInput("");
    try {
      const res = await apiPost<{ text: string; grounded_in?: string[] }>("/api/chatbot/ask", { question: q });
      setMessages((m) => [...m, { id: Date.now() + "-b", who: "bot", text: res.text, grounded: res.grounded_in }]);
    } catch {
      setMessages((m) => [...m, { id: Date.now() + "-b", who: "bot", text: "Couldn't reach the server. Check the API address in Profile." }]);
    }
    setTimeout(() => listRef.current?.scrollToEnd({ animated: true }), 100);
  }

  return (
    <>
      <Pressable style={styles.fab} onPress={() => setOpen(true)}>
        <Text style={styles.fabIcon}>💬</Text>
      </Pressable>
      <Modal visible={open} animationType="slide" transparent onRequestClose={() => setOpen(false)}>
        <View style={styles.overlay}>
          <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={styles.sheet}>
            <View style={styles.header}>
              <Text style={styles.headerText}>Ask Vyavastha</Text>
              <Pressable onPress={() => setOpen(false)}>
                <Text style={styles.close}>✕</Text>
              </Pressable>
            </View>
            <FlatList
              ref={listRef}
              data={messages}
              keyExtractor={(m) => m.id}
              contentContainerStyle={{ padding: spacing.md, gap: 8 }}
              renderItem={({ item }) => (
                <View style={[styles.bubble, item.who === "user" ? styles.bubbleUser : styles.bubbleBot]}>
                  <Text style={item.who === "user" ? styles.bubbleUserText : styles.bubbleBotText}>{item.text}</Text>
                  {!!item.grounded?.length && (
                    <Text style={styles.grounded}>GROUNDED IN: {item.grounded.join(", ")}</Text>
                  )}
                </View>
              )}
            />
            <View style={styles.inputRow}>
              <TextInput
                style={styles.input}
                placeholder="Ask a question..."
                placeholderTextColor={colors.muted}
                value={input}
                onChangeText={setInput}
                onSubmitEditing={send}
                returnKeyType="send"
              />
              <Pressable style={styles.sendBtn} onPress={send}>
                <Text style={styles.sendText}>Send</Text>
              </Pressable>
            </View>
          </KeyboardAvoidingView>
        </View>
      </Modal>
    </>
  );
}

const styles = StyleSheet.create({
  fab: {
    position: "absolute",
    bottom: 20,
    right: 20,
    width: 56,
    height: 56,
    borderRadius: 28,
    backgroundColor: colors.accent,
    alignItems: "center",
    justifyContent: "center",
    shadowColor: colors.accent,
    shadowOpacity: 0.35,
    shadowRadius: 10,
    shadowOffset: { width: 0, height: 4 },
    elevation: 6,
  },
  fabIcon: { fontSize: 24 },
  overlay: { flex: 1, backgroundColor: "rgba(18,59,109,0.35)", justifyContent: "flex-end" },
  sheet: { backgroundColor: colors.panel, borderTopLeftRadius: radius.xl, borderTopRightRadius: radius.xl, height: "70%" },
  header: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    padding: spacing.md,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
    backgroundColor: colors.pastelGreen,
    borderTopLeftRadius: radius.xl,
    borderTopRightRadius: radius.xl,
  },
  headerText: { fontWeight: "800", fontSize: 12, letterSpacing: 1, textTransform: "uppercase", color: colors.ink },
  close: { fontSize: 16, color: colors.muted },
  bubble: { maxWidth: "85%", padding: 10, borderRadius: radius.md },
  bubbleUser: { alignSelf: "flex-end", backgroundColor: colors.accent },
  bubbleUserText: { color: "#fff", fontWeight: "600", fontSize: 13 },
  bubbleBot: { alignSelf: "flex-start", backgroundColor: colors.bg, borderWidth: 1, borderColor: colors.border },
  bubbleBotText: { color: colors.ink, fontSize: 13 },
  grounded: { fontSize: 8.5, color: colors.accent, marginTop: 4 },
  inputRow: { flexDirection: "row", gap: 6, padding: spacing.md, borderTopWidth: 1, borderTopColor: colors.border },
  input: { flex: 1, borderWidth: 1, borderColor: colors.border, borderRadius: radius.md, paddingHorizontal: 12, color: colors.ink },
  sendBtn: { backgroundColor: colors.accent, borderRadius: radius.md, paddingHorizontal: 16, justifyContent: "center" },
  sendText: { color: "#fff", fontWeight: "800", fontSize: 12 },
});
