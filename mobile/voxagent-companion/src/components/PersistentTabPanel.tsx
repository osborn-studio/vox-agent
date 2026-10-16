import React from "react";
import { StyleSheet, View } from "react-native";

export function PersistentTabPanel({
  active,
  children,
}: {
  active: boolean;
  children: React.ReactNode;
}) {
  return (
    <View
      style={[styles.panel, !active && styles.hidden]}
      pointerEvents={active ? "auto" : "none"}
      accessibilityElementsHidden={!active}
      importantForAccessibility={active ? "auto" : "no-hide-descendants"}
    >
      {children}
    </View>
  );
}

const styles = StyleSheet.create({
  panel: {
    flex: 1,
  },
  hidden: {
    display: "none",
  },
});
