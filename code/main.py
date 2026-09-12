#!/usr/bin/env python3
"""Deterministic, evidence-aware Buy or Wait financial agent."""
from __future__ import annotations
import argparse, calendar, csv, itertools, json, re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

COLS=["request_id","amount_safe_to_pay","affordability_status","recommended_payment_method","payment_plan","earliest_date_for_full_payment","spending_changes_needed","decision_explanation"]
def rows(p):
    with p.open(encoding="utf-8",newline="") as f:return list(csv.DictReader(f))
def d(s):return date.fromisoformat(s)
def num(s):return None if s is None or str(s).strip()=="" else float(s)
def split(s):return set(str(s or "").split("|"))-{"","nan"}
def money(x):
    q=Decimal(str(max(0,x))).quantize(Decimal("0.01"),rounding=ROUND_HALF_UP)
    return format(q,"f").rstrip("0").rstrip(".")
def add_month(x):
    m=x.month; y=x.year+m//12; m=m%12+1
    return date(y,m,min(x.day,calendar.monthrange(y,m)[1]))
@dataclass(frozen=True)
class Flow:
    day:date;amount:float;event_id:str;category:str;direction:str
    flexible:str="fixed";minimum:float|None=None;recurring_key:str=""

class Agent:
    def __init__(self,dataset):
        self.dataset=dataset;self.profiles={x["user_id"]:x for x in rows(dataset/"financial_profiles.csv")}
        self.events=rows(dataset/"financial_events.csv");self.requests=rows(dataset/"requests.csv");self.samples=rows(dataset/"sample_requests.csv")
        self.options=defaultdict(list)
        for x in rows(dataset/"request_payment_options.csv"):self.options[x["request_id"]].append(x)
        self.messages=defaultdict(list)
        for x in rows(dataset/"messages.csv"):self.messages[x["user_id"]].append(x)
        links={x["related_event_id"]:x["image_id"] for x in rows(dataset/"images.csv")}
        evidence=Path(__file__).with_name("image_evidence.json"); amounts=json.loads(evidence.read_text()) if evidence.exists() else {}
        self.rates={(d(x["rate_date"]),x["from_currency"],x["to_currency"]):float(x["rate"]) for x in rows(dataset/"exchange_rates.csv")}
        self.by_user=defaultdict(list)
        for x in self.events:
            if not x["amount"] and x["event_id"] in links:
                x=x.copy();x["amount"]=str(amounts.get(links[x["event_id"]],""))
            self.by_user[x["user_id"]].append(x)
    def convert(self,a,cur,home,day):
        if cur==home:return a
        if (day,cur,home) in self.rates:return a*self.rates[(day,cur,home)]
        if (day,home,cur) in self.rates:return a/self.rates[(day,home,cur)]
        z=[(abs((rd-day).days),r) for (rd,f,t),r in self.rates.items() if f==cur and t==home]
        if z:return a*min(z)[1]
        z=[(abs((rd-day).days),r) for (rd,f,t),r in self.rates.items() if f==home and t==cur]
        if z:return a/min(z)[1]
        raise ValueError(f"missing supplied rate {cur}->{home} {day}")
    def message_rules(self,u,rd,home):
        o={"salary_amount":None,"salary_date":None,"salary_ended":False,"salary_once":False,"rent_multiplier":1.0,"extra_credits":[]}
        for m in sorted(self.messages[u],key=lambda x:x["sent_at"]):
            if datetime.fromisoformat(m["sent_at"].replace("Z","+00:00")).date()>rd:continue
            t=m["message_text"].lower()
            if any(q in t for q in ["employment has ended","contract has ended","kontrak musiman saat ini telah berakhir"]):o["salary_ended"]=True
            if ("rent" in t or "sewa" in t) and "12%" in t:o["rent_multiplier"]=1.12
            vals=re.findall(r"\b(INR|IDR|ZAR|USD|EUR)\s*([0-9]+(?:\.[0-9]+)?)",m["message_text"],re.I); dates=re.findall(r"\b(20\d\d-\d\d-\d\d)\b",m["message_text"])
            if vals and any(w in t for w in ["salary","gaji","payroll","penggajian"]) and not any(w in t for w in ["bonus","commission","komisi"]):
                cur,a=vals[0][0].upper(),float(vals[0][1]);when=d(dates[0]) if dates else None
                o["salary_amount"]=self.convert(a,cur,home,when or rd);o["salary_date"]=when;o["salary_ended"]=False
                o["salary_once"]=any(w in t for w in ["temporary monthly","next salary is reduced","gaji bulanan sementara","gaji berikutnya dikurangi"])
            if vals and any(w in t for w in ["invoice payment","pembayaran faktur"]):
                cur,a=vals[0][0].upper(),float(vals[0][1]);when=d(dates[0]) if dates else None
                if when:o["extra_credits"].append((when,self.convert(a,cur,home,when),m["message_id"]))
        return o
    def amount(self,e,home):
        a=num(e["amount"]);return None if a is None else self.convert(a,e["currency"],home,d(e["settlement_date"]))
    def base_flows(self,u,rd,end):
        home=self.profiles[u]["home_currency"];rules=self.message_rules(u,rd,home);hist=[];explicit=[]
        for e in self.by_user[u]:
            if not e["settlement_date"] or e["status"] in {"cancelled","failed","unrealized"}:continue
            day=d(e["settlement_date"]);a=self.amount(e,home)
            if a is None:continue
            if day<rd and e["status"]=="settled":hist.append((e,day,a))
            elif rd<=day<=end:
                ok=e["direction"]=="debit" and e["status"] in {"pending","scheduled"} or e["direction"]=="credit" and e["status"]=="scheduled"
                if ok and not (e["direction"]=="credit" and e["category"]=="salary" and rules["salary_ended"]):explicit.append(Flow(day,a,e["event_id"],e["category"],e["direction"],e["flexibility"],num(e["minimum_allowed_amount"])))
        groups=defaultdict(list)
        for e,day,a in hist:groups[(e["category"],e["direction"],e["event_type"],e["description"])].append((e,day,a))
        salary_hist=sorted([(day,e,a) for e,day,a in hist if e["category"]=="salary"],key=lambda x:x[0])
        if salary_hist and "final" in salary_hist[-1][1]["description"].lower():rules["salary_ended"]=True
        inferred=[]
        for (cat,direction,_,description),g in groups.items():
            g.sort(key=lambda x:x[1]);unique=sorted(set(x[1] for x in g));diffs=[(b-a).days for a,b in zip(unique,unique[1:])][-8:]
            if len(g)<3 or len(diffs)<2:continue
            med=sorted(diffs)[len(diffs)//2];monthly=27<=med<=35
            if med<5 or med>35:continue
            le,last,_=g[-1];vals=[x[2] for x in g]
            if (rd-last).days>40:continue
            if direction=="credit":
                if cat!="salary":continue
                if any(w in description.lower() for w in ["commission","bonus"]):continue
                if cat=="salary" and rules["salary_ended"]:continue
                regular=max(set(vals),key=vals.count)
                forecast=rules["salary_amount"] if cat=="salary" and rules["salary_amount"] is not None else vals[-1]
            else:
                forecast=max(vals[-3:])*(rules["rent_multiplier"] if cat in {"rent","housing"} else 1)
            nxt=add_month(last) if monthly else last+timedelta(days=med)
            if cat=="salary" and rules["salary_date"] and rules["salary_date"]>=rd:nxt=rules["salary_date"]
            count=0
            while nxt<=end:
                if nxt>=rd and not any(x.category==cat and x.direction==direction and abs((x.day-nxt).days)<=2 for x in explicit):
                    inferred.append(Flow(nxt,forecast,le["event_id"],cat,direction,le["flexibility"],num(le["minimum_allowed_amount"]),le["event_id"]))
                count+=1
                if cat=="salary" and rules["salary_once"] and count>=1:forecast=regular
                nxt=add_month(nxt) if monthly else nxt+timedelta(days=med)
        # A scheduled salary is explicit confirmation that the same monthly
        # payroll continues; extend it unless a newer message ends employment.
        scheduled_salary=sorted([x for x in explicit if x.category=="salary" and x.direction=="credit"],key=lambda x:x.day)
        if scheduled_salary and not rules["salary_ended"]:
            seed=scheduled_salary[-1];nxt=add_month(seed.day)
            existing={(x.day,x.category,x.direction) for x in explicit+inferred}
            while nxt<=end:
                if (nxt,"salary","credit") not in existing:inferred.append(Flow(nxt,seed.amount,seed.event_id,"salary","credit",recurring_key=seed.event_id))
                nxt=add_month(nxt)
        for day,a,eid in rules["extra_credits"]:
            if rd<=day<=end and not any(x.direction=="credit" and abs((x.day-day).days)<=1 and abs(x.amount-a)<.01 for x in explicit+inferred):explicit.append(Flow(day,a,eid,"invoice_income","credit"))
        return explicit+inferred
    @staticmethod
    def low(start,flows,payments=(),changes=()):
        stop={x[1] for x in changes if x[0]=="stop"};reduce={x[1]:x[2] for x in changes if x[0]=="reduce"};items=[]
        for f in flows:
            a=0 if f.recurring_key in stop else reduce.get(f.recurring_key,f.amount)
            items.append((f.day,2 if f.direction=="debit" else 0,-a if f.direction=="debit" else a))
        items += [(day,1,-a) for day,a in payments];bal=lowest=start
        for _,_,delta in sorted(items):bal+=delta;lowest=min(lowest,bal)
        return lowest
    def safe(self,start,minimum,flows,payments=(),changes=()):return self.low(start,flows,payments,changes)>=minimum-.005
    def change_sets(self,u,flows):
        p=self.profiles[u];red=split(p["expense_categories_user_is_willing_to_reduce"]);stop=split(p["expense_categories_user_is_willing_to_stop"]);latest={}
        for f in flows:
            if f.recurring_key:latest[f.recurring_key]=f
        acts=[]
        for key,f in latest.items():
            if f.category in stop and f.flexible in {"stoppable","reducible_or_stoppable"}:acts.append(("stop",key,0,f.event_id))
            if f.category in red and f.flexible in {"reducible","reducible_or_stoppable"} and f.minimum is not None and f.minimum<f.amount:acts.append(("reduce",key,f.minimum,f.event_id))
        out=[()]
        for n in range(1,min(3,len(acts))+1):
            out += [c for c in itertools.combinations(acts,n) if len({x[1] for x in c})==n]
        return out
    def evaluate(self,r):
        u=r["user_id"];p=self.profiles[u];rd=d(r["request_date"]);end=rd+timedelta(days=90);deadline=d(r["desired_completion_date"]);start=float(p["current_available_balance"]);minimum=float(p["minimum_balance_to_keep"]);requested=float(r["requested_amount"]);cur=p["home_currency"]
        flows=self.base_flows(u,rd,end);safe_today=min(requested,max(0,self.low(start,flows)-minimum));safe_today=float(Decimal(str(safe_today)).quantize(Decimal("0.01"),rounding=ROUND_HALF_UP))
        earliest=None
        for i in range(91):
            day=rd+timedelta(days=i)
            if self.safe(start,minimum,flows,[(day,requested)]):earliest=day;break
        prefs=split(p["payment_methods_user_will_consider"]);cands=[]
        def add(method,pays,total,changes=(),oid=""):
            if pays and max(x[0] for x in pays)<=deadline and self.safe(start,minimum,flows,pays,changes):cands.append(dict(method=method,pays=pays,total=total,changes=changes,oid=oid))
        if earliest and earliest>rd and earliest<=deadline and "full_payment" in prefs:add("wait",[(earliest,requested)],requested)
        for changes in self.change_sets(u,flows):
            if "full_payment" in prefs:add("full_payment",[(rd,requested)],requested,changes)
            if "installments" in prefs:
                mx=num(p["max_installment_months"])
                for o in self.options[r["request_id"]]:
                    n=int(o["number_of_payments"])
                    if o["payment_method"]!="installments" or mx is not None and n>mx:continue
                    first=d(o["first_payment_date"]);freq=int(float(o["payment_frequency_days"]));a=float(o["payment_amount"])
                    add("installments",[(first+timedelta(days=freq*i),a) for i in range(n)],float(o["total_payable_amount"]),changes,o["payment_option_id"])
            if "partial_payment" in prefs and r["allows_partial_payment"].lower()=="true" and 0<safe_today<requested and earliest and earliest<=deadline:add("partial_payment",[(rd,safe_today),(earliest,requested-safe_today)],requested,changes)
        if cands:
            cands.sort(key=lambda x:(bool(x["changes"]),x["total"],x["pays"][0][0],len(x["pays"]),x["oid"]));c=cands[0];method=c["method"];pays=c["pays"];changes=c["changes"]
            if method=="wait":status="affordable_later"
            else:status="affordable_now" if method=="full_payment" and not changes and safe_today>=requested-.005 else "affordable_with_plan"
        elif earliest and earliest<=deadline and "full_payment" in prefs:method="wait";pays=[(earliest,requested)];changes=();status="affordable_later"
        else:method="not_recommended";pays=[];changes=();status="not_affordable"
        plan="none" if not pays else "|".join(f"{day.isoformat()}:{money(a)}" for day,a in pays)
        ch=[f"stop:{eid}" if typ=="stop" else f"reduce_to:{eid}:{money(v)}" for typ,key,v,eid in changes];change_text="|".join(ch) if ch else "none";rq=money(requested);mn=money(minimum)
        if method=="full_payment" and changes:ex=f"Make the payment after the listed flexible-spending change(s). The plan pays {cur} {rq} and keeps the {cur} {mn} minimum protected."
        elif method=="full_payment":ex=f"Pay {cur} {rq} today. This keeps at least the {cur} {mn} minimum available over the next 90 days."
        elif method=="installments":ex=f"Use {len(pays)} installments starting {pays[0][0].strftime('%-d %B %Y')}. The complete plan keeps the {cur} {mn} minimum protected."
        elif method=="partial_payment":ex=f"Pay {cur} {money(pays[0][1])} today and the remaining {cur} {money(pays[1][1])} on {pays[1][0].strftime('%-d %B %Y')}; the {cur} {mn} minimum remains protected."
        elif method=="wait":ex=f"Wait until {earliest.strftime('%-d %B %Y')}, then pay {cur} {rq} in full. Paying earlier would put the {cur} {mn} minimum at risk."
        else:ex=f"Do not proceed by {deadline.strftime('%-d %B %Y')}. No eligible payment plan keeps the {cur} {mn} minimum protected."
        return dict(request_id=r["request_id"],amount_safe_to_pay=money(safe_today),affordability_status=status,recommended_payment_method=method,payment_plan=plan,earliest_date_for_full_payment=earliest.isoformat() if earliest else "",spending_changes_needed=change_text,decision_explanation=ex)

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--dataset",type=Path);ap.add_argument("--output",type=Path);ap.add_argument("--samples",action="store_true");a=ap.parse_args();root=Path(__file__).resolve().parents[1];dataset=a.dataset or root/"dataset";output=a.output or root/"output.csv";agent=Agent(dataset);source=agent.samples if a.samples else agent.requests;results=[agent.evaluate(r) for r in source]
    with output.open("w",encoding="utf-8",newline="") as f:w=csv.DictWriter(f,fieldnames=COLS);w.writeheader();w.writerows(results)
    print(f"Wrote {len(results)} predictions to {output}")
if __name__=="__main__":main()
