import { useState } from "react";
import { TxRoot, TxSegmented, TxToastProvider, TxTooltipProvider } from "@talyx/sdk";
import { useModules } from "./communityData";
import { Discover } from "./CommunityDiscover";
import { Installed } from "./CommunityInstalled";
import { Glass } from "./communityParts";
import "./CommunityScreen.css";

type Half = "discover" | "installed";
/* Community: Discover (the official modules and what people share) and Installed (module management).
   Both halves stay mounted, so switching keeps the place in each. */
export default function CommunityScreen({ onOpenModule, initialFocus }: { onOpenModule: (id: string) => void; initialFocus: string | null }) {
  const [half, setHalf] = useState<Half>(initialFocus ? "installed" : "discover");
  const [focus, setFocus] = useState(initialFocus);
  const { modules, error, reload } = useModules();

  return <TxRoot className="cm-root">
    <TxToastProvider>
      <TxTooltipProvider>
        <div className="cm-shell">
          <header className="cm-head">
            <Glass><TxSegmented label="Community" value={half} onChange={setHalf} options={[
              { value: "discover", label: "Discover" },
              { value: "installed", label: "Installed" },
            ]} /></Glass>
          </header>
          <div className="cm-body" hidden={half !== "discover"}>
            <Discover modules={modules ?? []} onOpenOfficial={onOpenModule} onManage={id => { setFocus(id); setHalf("installed"); }} />
          </div>
          <div className="cm-body" hidden={half !== "installed"}>
            <Installed key={focus ?? ""} modules={modules} error={error} onChanged={reload} focus={focus} />
          </div>
        </div>
      </TxTooltipProvider>
    </TxToastProvider>
  </TxRoot>;
}
