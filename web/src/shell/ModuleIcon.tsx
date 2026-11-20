import { TxIcon, txIconNames, type IconName } from "@talyx/sdk";
import { platformRuntime } from "../kernel/runtime";

/** A module id, or a panel kind ("module:<id>"). */
const ownerOf = (module: string) => module.startsWith("module:") ? module.slice("module:".length) : module;

/** The monochrome icon a module's first panel declares, from the component library's set; "blocks" without one. */
export function declaredModuleIcon(module: string): IconName {
  const declared = Object.values(platformRuntime().kernel.definitions.get(ownerOf(module))?.manifest.panels ?? {})[0]?.icon;
  return typeof declared === "string" && (txIconNames as readonly string[]).includes(declared) ? declared as IconName : "blocks";
}

/** A module's icon wherever modules are shown: the icon its panel declares. */
export function ModuleIcon({ module, className }: { module: string; className?: string }) {
  return <TxIcon name={declaredModuleIcon(module)} className={className} />;
}
