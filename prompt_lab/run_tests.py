"""Run the fit-check prompt against OCI Generative AI models and score the answers.

Usage: .venv/bin/python prompt_lab/run_tests.py [--runs 3] [--models a,b,c]
Reads ~/.oci/config (DEFAULT profile). Writes raw results to prompt_lab/results/.
"""
import argparse
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import oci
from oci.generative_ai_inference import GenerativeAiInferenceClient
from oci.generative_ai_inference import models as m

LAB = Path(__file__).parent
ENDPOINT = "https://inference.generativeai.us-chicago-1.oci.oraclecloud.com"
DEFAULT_MODELS = [
    "openai.gpt-oss-120b",
    "cohere.command-a-03-2025",
    "meta.llama-3.3-70b-instruct",
    "meta.llama-4-maverick-17b-128e-instruct-fp8",
]
CONSTRAINTS = [
    "power_frequency", "voltage_and_plug", "climate", "utilities", "consumables",
    "spare_parts", "training_and_repair", "grid_reliability", "manuals",
    "manufacturer_support", "customs_and_registration",
]
TESTS = {
    "A_planted": ("device_a_vx200.txt", "clinic_uganda.txt"),
    "C_vague": ("device_c_vague.txt", "clinic_uganda.txt"),
}


def build_prompt(device_file, clinic_file):
    template = (LAB / "prompt.txt").read_text()
    device = (LAB / "cases" / device_file).read_text().strip()
    clinic = (LAB / "cases" / clinic_file).read_text().strip()
    return template.replace("<<DEVICE>>", device).replace("<<CLINIC>>", clinic)


def call_model(client, compartment, model_id, prompt):
    if model_id.startswith("cohere."):
        req = m.CohereChatRequest(api_format="COHERE", message=prompt, temperature=0, max_tokens=4000)
    else:
        req = m.GenericChatRequest(
            api_format="GENERIC",
            messages=[m.UserMessage(content=[m.TextContent(text=prompt)])],
            temperature=0,
            max_tokens=8000 if model_id.startswith("openai.") else 4000,
        )
    details = m.ChatDetails(
        compartment_id=compartment,
        serving_mode=m.OnDemandServingMode(model_id=model_id),
        chat_request=req,
    )
    resp = client.chat(details).data.chat_response
    if model_id.startswith("cohere."):
        return resp.text
    return "".join(c.text for c in resp.choices[0].message.content if getattr(c, "text", None))


def parse(text):
    """Return (data, strict). strict means the reply was bare JSON with nothing around it."""
    try:
        return json.loads(text.strip()), True
    except (json.JSONDecodeError, AttributeError):
        pass
    match = re.search(r"\{.*\}", text or "", re.S)
    if match:
        try:
            return json.loads(match.group(0)), False
        except json.JSONDecodeError:
            pass
    return None, False


def score(test, data):
    s = {}
    by_name = {c.get("name"): c for c in data.get("constraints", []) if isinstance(c, dict)}
    status = lambda n: by_name.get(n, {}).get("status")
    s["all_present"] = all(n in by_name for n in CONSTRAINTS)
    s["verdict"] = verdict_from(by_name.values())
    s["model_agrees"] = data.get("verdict") == s["verdict"]
    if test == "A_planted":
        addon = (by_name.get("power_frequency", {}).get("addon") or "").lower()
        s["freq_trap"] = status("power_frequency") == "fail" or (
            status("power_frequency") == "needs_addon" and "frequency" in addon and "converter" in addon
        )
        s["climate_caught"] = status("climate") == "fail"
        s["support_caught"] = status("manufacturer_support") in ("fail", "needs_addon")
        s["customs_not_assumed"] = status("customs_and_registration") in ("unknown", "needs_addon")
        s["verdict_ok"] = s["verdict"] == "wont_work"
    if test == "C_vague":
        s["unknowns"] = sum(1 for c in by_name.values() if c.get("status") == "unknown")
        # Nothing is known about the device, so any "ok" or "fail" is a guess.
        s["no_guess"] = not any(c.get("status") in ("ok", "fail") for c in by_name.values())
        s["verdict_ok"] = s["verdict"] == "needs_info" and bool(data.get("questions_for_donor"))
    return s


def verdict_from(constraints):
    """The verdict the app will show: computed from the per-constraint statuses, not taken from the model."""
    statuses = {c.get("status") for c in constraints}
    if "fail" in statuses:
        return "wont_work"
    if "unknown" in statuses:
        return "needs_info"
    if "needs_addon" in statuses:
        return "works_if_you_send"
    return "works"


def run_one(client, compartment, model_id, test, run):
    prompt = build_prompt(*TESTS[test])
    start = time.time()
    rec = {"model": model_id, "test": test, "run": run}
    for attempt in range(3):
        try:
            text = call_model(client, compartment, model_id, prompt)
            break
        except oci.exceptions.ServiceError as e:
            if e.status == 429 and attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            rec.update(error=f"{e.status} {e.code}: {e.message}", seconds=round(time.time() - start, 1))
            return rec
    rec["seconds"] = round(time.time() - start, 1)
    rec["raw"] = text
    data, strict = parse(text)
    rec["json_strict"] = strict
    rec["json_ok"] = data is not None
    if data is not None:
        rec["parsed"] = data
        rec["score"] = score(test, data)
    return rec


def mark(v):
    return "✅" if v is True else "❌" if v is False else str(v)


def summarize(records, models, runs):
    lines = [
        "| Model | Strict JSON | All 11 | Model verdict = code | A: freq trap | A: climate | A: EOL support"
        " | A: customs not assumed | A: verdict | A: same verdict | C: needs_info | C: no guessed ok/fail"
        " | C: unknowns | Avg s | Errors |",
        "|" + "---|" * 15,
    ]
    for model in models:
        rs = [r for r in records if r["model"] == model]
        ok = [r for r in rs if r.get("json_ok")]
        a = [r for r in ok if r["test"] == "A_planted"]
        c = [r for r in ok if r["test"] == "C_vague"]
        errs = [r for r in rs if "error" in r]
        frac = lambda xs, key: f"{sum(1 for r in xs if r['score'].get(key))}/{len(xs)}"
        a_verdicts = {r["score"]["verdict"] for r in a}
        times = [r["seconds"] for r in rs if "error" not in r]
        lines.append("| " + " | ".join([
            model,
            f"{sum(1 for r in rs if r.get('json_strict'))}/{len(rs)}",
            frac(ok, "all_present"),
            frac(ok, "model_agrees"),
            frac(a, "freq_trap"),
            frac(a, "climate_caught"),
            frac(a, "support_caught"),
            frac(a, "customs_not_assumed"),
            frac(a, "verdict_ok"),
            mark(len(a_verdicts) == 1 and len(a) == runs) + (f" {sorted(map(str, a_verdicts))}" if len(a_verdicts) > 1 else ""),
            frac(c, "verdict_ok"),
            frac(c, "no_guess"),
            ",".join(str(r["score"]["unknowns"]) for r in c),
            f"{sum(times) / len(times):.1f}" if times else "-",
            f"{len(errs)}" + (f" ({errs[0]['error'][:60]})" if errs else ""),
        ]) + " |")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--models", default=",".join(DEFAULT_MODELS))
    args = ap.parse_args()
    models = args.models.split(",")

    cfg = oci.config.from_file()
    client = GenerativeAiInferenceClient(cfg, service_endpoint=ENDPOINT, timeout=(10, 240))
    jobs = [(mo, t, r) for mo in models for t in TESTS for r in range(1, args.runs + 1)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        records = list(pool.map(lambda j: run_one(client, cfg["tenancy"], *j), jobs))

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = LAB / "results" / f"{stamp}.json"
    out.write_text(json.dumps(records, indent=2, ensure_ascii=False))
    table = summarize(records, models, args.runs)
    (LAB / "results" / f"{stamp}.md").write_text(table + "\n")
    print(table)
    print(f"\nraw: {out}")


if __name__ == "__main__":
    main()
