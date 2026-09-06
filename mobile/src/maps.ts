import { Linking } from "react-native";

export function openInMaps(lat: number, lng: number) {
  Linking.openURL(`https://www.google.com/maps/dir/?api=1&destination=${lat},${lng}`);
}
