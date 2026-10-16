import { Share } from "react-native";

import {
  createMobileDiagnosticBundle,
  scanMobileDiagnosticBundle,
  serializeMobileDiagnosticBundle,
} from "./bundle";

export async function sharePrivacySafeDiagnostics(): Promise<boolean> {
  const bundle = createMobileDiagnosticBundle();
  if (scanMobileDiagnosticBundle(bundle).length > 0) {
    throw new Error("diagnostic_bundle_rejected");
  }
  const result = await Share.share({
    title: "Vox Agent diagnostics",
    message: serializeMobileDiagnosticBundle(bundle),
  });
  return result.action === Share.sharedAction;
}
