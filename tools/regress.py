"""Regression: every test over several seeds, merged functional coverage,
bug-injection runs (each injected RTL bug must make the suite fail) and SVA
checks (no assertion failure on clean RTL; every assertion fires on the bug
it targets).

usage: python3 tools/regress.py [--seeds N] [--scale X] [--bugs] [--sva-bugs] [--out results/DIR]
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

TB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tb")
BUGS = ["BUG_UART_PARITY", "BUG_UART_FIFO_DEPTH", "BUG_RX_SAMPLE", "BUG_UART_W1C", "BUG_UART_NOWAIT", "BUG_UART_BAUD_CLAMP",
        "BUG_TIMER_RELOAD", "BUG_TIMER_PRESC", "BUG_GPIO_W1C", "BUG_GPIO_EDGE_LOST", "BUG_INTC_MASK", "BUG_DECODE"]

RAL_BUGS = ["BUG_UART_W1C", "BUG_GPIO_W1C"]
# APB protocol bugs used only to show that the SVA protocol checks fire:
# "drv:<mode>" is a requester bug in the testbench driver (APB_DRV_BUG),
# BUG_APB_* is a completer bug in the RTL.
PROTO_BUGS = ["drv:penable_idle", "drv:no_setup", "drv:abort_setup", "drv:drop_wait", "drv:addr_change", "drv:wdata_change",
              "BUG_APB_SLVERR_SETUP", "BUG_APB_PREADY_SETUP"]
# assertion -> the injected bug that must make it fire (sva/soc_sva.sv)
SVA_TARGETS = {
    "APB_PENABLE_NEEDS_PSEL": "drv:penable_idle", "APB_FIRST_CYCLE_IS_SETUP": "drv:no_setup",
    "APB_PENABLE_DROPS_AFTER_READY": "drv:no_setup", "APB_SETUP_THEN_ACCESS": "drv:abort_setup",
    "APB_ACCESS_HOLDS_UNTIL_READY": "drv:drop_wait", "APB_ADDR_CTRL_STABLE": "drv:addr_change", "APB_WDATA_STABLE": "drv:wdata_change",
    "APB_SLVERR_ONLY_IN_ACCESS": "BUG_APB_SLVERR_SETUP", "APB_PREADY_ONLY_IN_ACCESS": "BUG_APB_PREADY_SETUP",
    "SOC_DECODE_HOLE_ERRORS": "BUG_DECODE", "SOC_IRQ_IS_MASKED_OR": "BUG_INTC_MASK",
    "UART_FIRST_ACCESS_CYCLE_WAITS": "BUG_UART_NOWAIT", "UART_TX_FULL_AT_DEPTH": "BUG_UART_FIFO_DEPTH", "UART_BAUD_AT_LEAST_4": "BUG_UART_BAUD_CLAMP",
    "UART_W1C_CLEARS_OVERRUN": "BUG_UART_W1C", "UART_TX_PARITY_CORRECT": "BUG_UART_PARITY", "UART_RX_SAMPLES_MID_BIT": "BUG_RX_SAMPLE",
    "TIMER_TICK_EVERY_PRESC_PLUS_1": "BUG_TIMER_PRESC", "TIMER_MATCH_RESTARTS_AT_0": "BUG_TIMER_RELOAD",
    "GPIO_W1C_CLEARS_ONLY_WRITTEN_BITS": "BUG_GPIO_W1C", "GPIO_EDGE_ALWAYS_CAPTURED": "BUG_GPIO_EDGE_LOST",
}
# X checks: Verilator is 2-state, so these cannot fail here (4-state simulators only)
SVA_X_ONLY = ["APB_NO_X_ON_REQUEST", "SOC_NO_X_ON_OUTPUTS"]


def sva_fired(out):
    return sorted(set(re.findall(r"SVA_FAIL (\w+)", out)))


def run(seed, scale, build, defines="", cov_dir=None, testcase=None, extra_env=None):
    env = dict(os.environ, RANDOM_SEED=str(seed), TEST_SCALE=str(scale), **(extra_env or {}))
    if cov_dir:
        os.makedirs(cov_dir, exist_ok=True)
        env["COV_DIR"] = os.path.abspath(cov_dir)
    args = ["make", "-s", "SIM=verilator", f"SIM_BUILD={build}", f"BUG_DEFINES={defines}", f"COCOTB_RESULTS_FILE=results_{seed}.xml"]
    if testcase:
        env["ONLY_TESTS"] = testcase
    t0 = time.time()
    p = subprocess.run(args, cwd=TB, env=env, capture_output=True, text=True)
    res = {}
    xml = os.path.join(TB, f"results_{seed}.xml")
    if os.path.exists(xml):
        for tc in ET.parse(xml).getroot().iter("testcase"):
            if tc.find("skipped") is not None:
                continue
            res[tc.get("name")] = "fail" if tc.find("failure") is not None or tc.find("error") is not None else "pass"
        os.remove(xml)
    if not res:
        res["<build or run>"] = "fail"
        sys.stderr.write(p.stdout[-3000:] + p.stderr[-3000:])
    return res, time.time() - t0, p.stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--bugs", action="store_true")
    ap.add_argument("--bug-seeds", type=int, default=2)
    ap.add_argument("--ral-bugs", action="store_true", help="W1C bugs must fail the RAL tests with the lockstep scoreboard and SVA off")
    ap.add_argument("--sva-bugs", action="store_true", help="APB protocol bugs: every SVA property must fire on its target bug")
    ap.add_argument("--out", default="results/latest")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    report = {"seeds": [], "coverage": None, "bugs": [], "sva_clean_failures": [], "proto_bugs": [], "sva_targets": None, "ral_bugs": []}
    merged = None
    ok = True
    for seed in range(1, a.seeds + 1):
        cov_dir = os.path.join(a.out, f"cov_seed{seed}")
        res, secs, out = run(seed, a.scale, "sim_build", cov_dir=cov_dir)
        fails = [t for t, r in res.items() if r != "pass"]
        report["sva_clean_failures"] += sva_fired(out)
        ok &= not fails
        report["seeds"].append({"seed": seed, "tests": res, "seconds": round(secs, 1)})
        print(f"seed {seed}: {sum(r == 'pass' for r in res.values())}/{len(res)} passed in {secs:.0f} s {fails or ''}", flush=True)
        for f in sorted(os.listdir(cov_dir)):
            c = json.load(open(os.path.join(cov_dir, f)))
            if merged is None:
                merged = {k: 0 for k in c["hits"]}
            for k, v in c["hits"].items():
                merged[k] += v
    hit = sum(1 for v in merged.values() if v)
    report["coverage"] = {"bins": len(merged), "hit": hit, "percent": 100 * hit / len(merged), "holes": [k for k, v in merged.items() if not v],
                          "hits": merged}
    print(f"SVA failures on clean RTL: {sorted(set(report['sva_clean_failures'])) or 'none'}")
    print(f"merged functional coverage: {hit}/{len(merged)} bins ({100 * hit / len(merged):.1f} %); holes: {report['coverage']['holes']}")
    if a.bugs:
        for bug in BUGS:
            caught, detail, fired = False, {}, set()
            for seed in range(1, a.bug_seeds + 1):
                # SVA_FATAL=0: tests fail only on testbench checks; SVA failures are logged separately
                res, secs, out = run(seed, a.scale, f"sim_build_{bug.lower()}", f"+define+{bug}", extra_env={"SVA_FATAL": "0"})
                failed = [t for t, r in res.items() if r != "pass"]
                detail[seed] = failed
                fired |= set(sva_fired(out))
                if failed:
                    caught = True
                    break
            report["bugs"].append({"bug": bug, "caught": caught, "failing_tests": detail, "sva_fired": sorted(fired)})
            print(f"{bug:22} {'caught by ' + ', '.join(detail[max(detail)]) if caught else 'MISSED'}; SVA: {', '.join(sorted(fired)) or '-'}", flush=True)
            shutil.rmtree(os.path.join(TB, f"sim_build_{bug.lower()}"), ignore_errors=True)
        missed = [b["bug"] for b in report["bugs"] if not b["caught"]]
        print(f"bugs caught: {len(BUGS) - len(missed)}/{len(BUGS)} {missed or ''}")
        ok &= not missed
    if a.sva_bugs:
        for bug in PROTO_BUGS:
            if bug.startswith("drv:"):
                res, secs, out = run(1, 0.3, "sim_build", testcase="RegisterTest", extra_env={"APB_DRV_BUG": bug[4:], "SVA_FATAL": "0"})
            else:
                res, secs, out = run(1, 0.3, f"sim_build_{bug.lower()}", f"+define+{bug}", testcase="RegisterTest", extra_env={"SVA_FATAL": "0"})
                shutil.rmtree(os.path.join(TB, f"sim_build_{bug.lower()}"), ignore_errors=True)
            fired = sva_fired(out)
            report["proto_bugs"].append({"bug": bug, "sva_fired": fired, "tests": res})
            print(f"{bug:22} SVA: {', '.join(fired) or '-'}", flush=True)
    if a.ral_bugs:
        # the RAL tests alone (no lockstep scoreboard, SVA not fatal) must catch the W1C bugs
        for bug in RAL_BUGS:
            res, secs, out = run(1, a.scale, f"sim_build_{bug.lower()}", f"+define+{bug}", testcase="RalRegisterTest,RalW1cTest",
                                 extra_env={"LOCKSTEP": "0", "SVA_FATAL": "0"})
            shutil.rmtree(os.path.join(TB, f"sim_build_{bug.lower()}"), ignore_errors=True)
            failed = [t for t, r in res.items() if r != "pass"]
            msgs = re.findall(r"(W1C of bit \d+: \w+ read \S+, mirror \S+)", out)
            report["ral_bugs"].append({"bug": bug, "failing_tests": failed, "ral_mismatches": msgs[:4]})
            print(f"{bug:22} RAL only: {'caught by ' + ', '.join(failed) if failed else 'MISSED'} {msgs[:1]}", flush=True)
            ok &= bool(failed)
    if a.bugs or a.sva_bugs:
        fired_by = {b["bug"]: set(b["sva_fired"]) for b in report["bugs"] + report["proto_bugs"]}
        targets = {}
        for prop, bug in SVA_TARGETS.items():
            targets[prop] = {"bug": bug, "fired": prop in fired_by[bug]} if bug in fired_by else {"bug": bug, "fired": None}
        report["sva_targets"] = targets
        checked = [p for p, t in targets.items() if t["fired"] is not None]
        silent = [p for p in checked if not targets[p]["fired"]]
        print(f"SVA properties that fired on their target bug: {len(checked) - len(silent)}/{len(checked)} {silent or ''}"
              f" (X-only, not testable on 2-state Verilator: {', '.join(SVA_X_ONLY)})")
        ok &= not silent
    ok &= not report["sva_clean_failures"]
    json.dump(report, open(os.path.join(a.out, "regression.json"), "w"), indent=1)
    sys.exit(0 if ok else 1)


main()
