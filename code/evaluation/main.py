#!/usr/bin/env python3
"""Structural validator plus public-sample regression report."""
import argparse,csv,sys
from datetime import date
from pathlib import Path

COLS=["request_id","amount_safe_to_pay","affordability_status","recommended_payment_method","payment_plan","earliest_date_for_full_payment","spending_changes_needed","decision_explanation"]
STAT={"affordable_now","affordable_with_plan","affordable_later","not_affordable"};METHOD={"full_payment","partial_payment","installments","wait","not_recommended"}
def load(p):
    with p.open(encoding="utf-8",newline="") as f:return list(csv.DictReader(f)),list(csv.DictReader(p.open(encoding="utf-8",newline=""))) if False else None
def main():
    ap=argparse.ArgumentParser();ap.add_argument("--output",type=Path,default=Path("output.csv"));ap.add_argument("--dataset",type=Path,default=Path("dataset"));ap.add_argument("--samples",action="store_true");a=ap.parse_args()
    with a.output.open(encoding="utf-8",newline="") as f:rdr=csv.DictReader(f); actual_cols=rdr.fieldnames; out=list(rdr)
    reqfile=a.dataset/("sample_requests.csv" if a.samples else "requests.csv")
    with reqfile.open(encoding="utf-8",newline="") as f:req=list(csv.DictReader(f))
    errors=[]
    if actual_cols!=COLS:errors.append(f"columns differ: {actual_cols}")
    if len(out)!=len(req):errors.append(f"row count {len(out)} != {len(req)}")
    rq={x["request_id"]:x for x in req}; opts=list(csv.DictReader((a.dataset/"request_payment_options.csv").open()))
    profiles={x["user_id"]:x for x in csv.DictReader((a.dataset/"financial_profiles.csv").open())}; events={x["event_id"]:x for x in csv.DictReader((a.dataset/"financial_events.csv").open())}
    for i,x in enumerate(out,2):
        rid=x["request_id"]
        if rid not in rq:errors.append(f"row {i}: unknown request_id");continue
        r=rq[rid]
        try:amt=float(x["amount_safe_to_pay"])
        except:errors.append(f"{rid}: invalid amount");continue
        if not 0<=amt<=float(r["requested_amount"]):errors.append(f"{rid}: amount out of bounds")
        if x["affordability_status"] not in STAT:errors.append(f"{rid}: bad status")
        if x["recommended_payment_method"] not in METHOD:errors.append(f"{rid}: bad method")
        if x["affordability_status"]=="affordable_now" and x["earliest_date_for_full_payment"]!=r["request_date"]:errors.append(f"{rid}: affordable_now date mismatch")
        pays=[]
        if x["payment_plan"]!="none":
            try:pays=[(date.fromisoformat(z.split(":",1)[0]),float(z.split(":",1)[1])) for z in x["payment_plan"].split("|")]
            except:errors.append(f"{rid}: malformed plan")
            if pays!=sorted(pays):errors.append(f"{rid}: plan not chronological")
        if x["recommended_payment_method"]=="partial_payment":
            if len(pays)!=2 or abs(sum(v for _,v in pays)-float(r["requested_amount"]))>.011:errors.append(f"{rid}: invalid partial plan")
        if x["recommended_payment_method"]=="installments":
            valid=False
            for o in (z for z in opts if z["request_id"]==rid and z["payment_method"]=="installments"):
                first=date.fromisoformat(o["first_payment_date"]);freq=int(float(o["payment_frequency_days"]));n=int(o["number_of_payments"]);expected=[(first.fromordinal(first.toordinal()+freq*j),float(o["payment_amount"])) for j in range(n)]
                if len(pays)==len(expected) and all(a==b and abs(v-w)<.011 for (a,v),(b,w) in zip(pays,expected)):valid=True
            if not valid:errors.append(f"{rid}: installment does not match an offer")
        if x["spending_changes_needed"]!="none":
            p=profiles[r["user_id"]]
            for change in x["spending_changes_needed"].split("|"):
                parts=change.split(":");e=events.get(parts[1]) if len(parts)>1 else None
                if not e or e["flexibility"]=="fixed":errors.append(f"{rid}: invalid flexible event {change}")
        if not x["decision_explanation"].strip():errors.append(f"{rid}: empty explanation")
    if a.samples:
        truth={x["request_id"]:x for x in req}; fields=COLS[1:7]
        print("Public sample exact-match metrics")
        for f in fields:print(f"  {f}: {sum(x[f]==truth[x['request_id']][f] for x in out)}/{len(out)}")
    if errors:
        print("INVALID");print("\n".join("- "+x for x in errors[:50]));sys.exit(1)
    print(f"VALID: {len(out)} rows, exact schema and structural constraints satisfied")
if __name__=="__main__":main()
