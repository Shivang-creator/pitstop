import { useEffect, useState } from "react";
import {
  buildPlan,
  getCapabilities,
  runApply,
  runScan,
  type ApplyResult,
  type Capabilities,
  type PlanResult,
  type Progress,
  type ScanResult,
} from "./api";
import { Discover, Draft } from "./components/Discover";
import { Landing, Scanning } from "./components/Landing";
import { PlanView } from "./components/PlanView";
import { Report } from "./components/Report";

type Stage = "landing" | "scanning" | "report" | "plan" | "discover" | "draft";

export default function App() {
  const [stage, setStage] = useState<Stage>("landing");
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [scan, setScan] = useState<ScanResult | null>(null);
  const [plan, setPlan] = useState<PlanResult | null>(null);
  const [applyResult, setApplyResult] = useState<ApplyResult | null>(null);
  const [planning, setPlanning] = useState(false);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getCapabilities()
      .then(setCapabilities)
      .catch((e: Error) => setError(e.message));
  }, []);

  async function handleScan(
    channel: string,
    fixture: string | null,
    limit: number | null,
  ) {
    setError(null);
    setProgress(null);
    setScan(null);
    setPlan(null);
    setApplyResult(null);
    setStage("scanning");

    try {
      const result = await runScan(
        { channel, fixture, limit, owner: capabilities?.has_token ?? false },
        setProgress,
      );
      setScan(result);
      setStage("report");
    } catch (e) {
      setError((e as Error).message);
      setStage("landing");
    }
  }

  async function handlePlan() {
    if (!scan) return;
    setPlanning(true);
    setError(null);
    try {
      setPlan(await buildPlan(scan.scan_id));
      setStage("plan");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPlanning(false);
    }
  }

  async function handleApply(dryRun: boolean) {
    if (!scan) return;
    setApplying(true);
    setProgress(null);
    try {
      const result = await runApply(
        scan.scan_id,
        { confirm: !dryRun, dry_run: dryRun },
        setProgress,
      );
      setApplyResult(result);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setApplying(false);
    }
  }

  function reset() {
    setStage("landing");
    setScan(null);
    setPlan(null);
    setApplyResult(null);
    setError(null);
  }

  if (stage === "scanning") return <Scanning progress={progress} />;

  if (stage === "discover") return <Discover onBack={reset} />;

  if (stage === "draft") return <Draft onBack={reset} />;

  if (stage === "plan" && scan && plan)
    return (
      <PlanView
        scan={scan}
        plan={plan}
        onApply={handleApply}
        onBack={() => {
          setApplyResult(null);
          setStage("report");
        }}
        applying={applying}
        progress={progress}
        result={applyResult}
      />
    );

  if (stage === "report" && scan)
    return (
      <Report
        scan={scan}
        onPlan={handlePlan}
        onReset={reset}
        planning={planning}
      />
    );

  return (
    <Landing
      onScan={handleScan}
      onDiscover={() => setStage("discover")}
      onDraft={() => setStage("draft")}
      capabilities={capabilities}
      error={error}
    />
  );
}
