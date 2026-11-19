#!/usr/bin/env python3
"""Large fictional eval corpus: 5 docs x 4 sections, 40 questions (30 answerable + 10 not-in-docs).
Retrieval-only eval (BM25 offline): python3 tests/build_big_corpus.py  -> writes corpus_big/*.md, big_cases.json, prints hit@1/hit@3.
All content FICTIONAL. Questions are deliberately paraphrased (few shared words) to stress keyword search."""
import json, pathlib, sys
H = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(H.parent))
DOCS = {
 "hvac": ("RTU-20 Rooftop Unit Service Sheet", [
  ("Low refrigerant", "Suction pressure below 105 psi with icing on the coil means low refrigerant charge. Find the leak before adding R-410A. Recover and weigh in 9.5 lb."),
  ("Compressor won't start", "If the contactor pulls in but the compressor hums, check the start capacitor. Replace with a 45/5 uF 440V dual-run capacitor."),
  ("Supply fan belt", "Belt deflection should be 0.5 inch at mid-span. Replace belts in matched sets. Tighten motor mount bolts to 35 ft-lb."),
  ("Filter change", "Use 20x25x4 MERV 11 filters. Change every 90 days, or when pressure drop across the filter reaches 0.8 inch w.c.")]),
 "elec": ("Panel LP-3 Maintenance Procedure", [
  ("Lockout before opening", "De-energize breaker 12 and apply a red lock and tag. Test for absence of voltage with a CAT III meter on all three phases before touching anything."),
  ("Breaker torque", "Torque 60 A breaker lugs to 25 in-lb. Torque the main lugs to 275 in-lb. Re-check after 24 hours of load."),
  ("Thermal scan", "Infrared scan under at least 40 percent load. A lug more than 15 degrees C above its neighbors needs retightening within 7 days."),
  ("Arc flash label", "Incident energy at this panel is 4.2 cal/cm2 at 18 inches. Wear category 2 PPE with a face shield.")]),
 "cnc": ("Haas VF-2 Operator Quick Card", [
  ("Spindle warm-up", "Run the spindle warm-up program for 10 minutes at 2000 RPM if the machine has been idle more than 4 hours."),
  ("Tool offset", "Touch off with the probe: press Offset, select the tool number, then Z Face Measure. Tool length offsets must be within 0.002 inch of the setup sheet."),
  ("Coolant", "Mix coolant at 8 percent concentration with a refractometer. Replace the tank every 6 months. Skim tramp oil weekly."),
  ("Alarm 108 servo overload", "Alarm 108 means axis servo overload. Reduce feed rate, check for a crashed way cover, and clean the chip pile. Reset with the power-up key.")]),
 "forklift": ("Electric Forklift FL-25 Daily Check", [
  ("Battery", "Battery electrolyte must cover the plates. Charge at 80 percent depth of discharge. Never let the voltage drop under 1.75 V per cell."),
  ("Forks", "Retire forks if the heel wear exceeds 10 percent of original thickness. Check the fork angle; it should be within 3 degrees of 90."),
  ("Tire", "Cushion tires: replace when worn to the wear line molded in the sidewall. Inflate pneumatic tires to 115 psi."),
  ("Load rating", "Rated capacity is 5000 lb at a 24 inch load center. Do not lift above 130 inches with the standard mast.")]),
 "weld": ("MIG Welding Station Setup Card", [
  ("Shielding gas", "Use 75/25 argon CO2 at 25 CFH for mild steel. Check the hose for leaks with soapy water."),
  ("Wire feed", "For 0.035 wire on 1/4 inch plate, set 18 volts and 350 inches per minute wire speed. Replace the drive roll if it slips."),
  ("Porosity", "Pinholes in the bead usually mean gas coverage was lost. Check the nozzle for spatter and for drafts from fans."),
  ("Fire watch", "Keep a fire watch for 30 minutes after welding. Remove combustibles within 35 feet. Extinguisher is on the north wall.")]),
}
Q = [ # (doc, section idx, question, expected substring)
 ("hvac",0,"Coil is frosting up and suction is 98. What does that point to?","leak"),
 ("hvac",0,"How much refrigerant goes back in after the repair?","9.5"),
 ("hvac",1,"Compressor buzzes but won't turn. What part do I swap?","capacitor"),
 ("hvac",2,"How tight should the fan drive strap be?","0.5"),
 ("hvac",3,"What size air filters does this unit take?","20x25x4"),
 ("hvac",3,"When is the filter considered clogged?","0.8"),
 ("elec",0,"What do I do before opening the panel door?","lock"),
 ("elec",1,"What torque for the 60 amp breaker terminals?","25"),
 ("elec",2,"How hot is too hot compared to the neighboring terminals?","15"),
 ("elec",3,"What protective gear for the shock and blast hazard here?","category 2"),
 ("elec",2,"Under what load should I do the heat camera inspection?","40"),
 ("cnc",0,"Machine sat overnight. What should I run first?","10 minutes"),
 ("cnc",1,"How do I set the length of a new cutter?","Z Face"),
 ("cnc",2,"What's the right mix ratio for the cutting fluid?","8 percent"),
 ("cnc",3,"Servo overload alarm, what now?","feed rate"),
 ("cnc",2,"How often do I clean oil off the top of the sump?","weekly"),
 ("forklift",0,"What's the lowest safe cell voltage on the truck battery?","1.75"),
 ("forklift",1,"When are the tines too worn to use?","10 percent"),
 ("forklift",2,"What pressure for the air-filled wheels?","115"),
 ("forklift",3,"Max weight I can lift?","5000"),
 ("forklift",3,"How high can I raise the load?","130"),
 ("weld",0,"Which gas mix for steel?","75/25"),
 ("weld",1,"What voltage for quarter inch plate?","18"),
 ("weld",2,"Little holes in my weld. Why?","gas"),
 ("weld",3,"How long do I stay watching for sparks after finishing?","30 minutes"),
 ("weld",3,"Where is the extinguisher?","north wall"),
 ("hvac",1,"What microfarad rating for the run cap?","45/5"),
 ("elec",1,"What about the main lugs torque?","275"),
 ("cnc",1,"How accurate must the tool length be versus the setup sheet?","0.002"),
 ("forklift",1,"How far off square can the fork angle be?","3 degrees"),
]
NEG = ["What is the cafeteria menu today?","Who is the plant manager?","What is the torque for the crane hook?","How do I reset the building fire alarm?",
 "What is the PTO policy?","What oil does the air compressor use?","What is the wifi password?","How do I weld aluminum with TIG?",
 "What is the boiler operating pressure?","How do I order safety glasses?"]
def main():
    (H/"corpus_big").mkdir(exist_ok=True)
    for k,(title,secs) in DOCS.items():
        t=f"# {title} (fictional)\n\n"
        for i,(h,b) in enumerate(secs): t+=f"<!-- page: {i+1} -->\n## {h}\n\n{b}\n\n"
        (H/"corpus_big"/f"{k}.md").write_text(t)
    cases=[{"id":i+1,"q":q,"doc":d,"page":str(s+1),"any":[a]} for i,(d,s,q,a) in enumerate(Q)]
    cases+=[{"id":len(Q)+i+1,"q":q,"doc":None,"page":None,"any":[]} for i,q in enumerate(NEG)]
    (H/"big_cases.json").write_text(json.dumps(cases,indent=1))
    from retrieval import search
    chunks=[]
    for k,(title,secs) in DOCS.items():
        for i,(h,b) in enumerate(secs): chunks.append({"chunk_id":f"{k}-{i}","doc_id":k,"title":title,"page":str(i+1),"text":h+". "+b,"emb":None})
    h1=h3=0; neg_ref=0
    for c in cases:
        hits=search(chunks,c["q"],k=3)
        if c["doc"]:
            ok=[(x["doc_id"],x["page"]) for x in hits]
            h1+= ok[:1]==[(c["doc"],c["page"])]; h3+= (c["doc"],c["page"]) in ok
            if (c["doc"],c["page"]) not in ok: print("MISS",c["q"],"->",ok)
        else: neg_ref+= not hits
    n=len(Q); print(f"BM25 hit@1 {h1}/{n}  hit@3 {h3}/{n}  negatives gated {neg_ref}/{len(NEG)}")
main()
