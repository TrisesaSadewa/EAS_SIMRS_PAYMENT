import os
import uvicorn
from datetime import datetime
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from supabase import create_client, Client

# --- CONFIGURATION ---
SUPABASE_URL = "https://esmhvcfemenpmpciiucz.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImVzbWh2Y2ZlbWVucG1wY2lpdWN6Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2NTc4OTcwOCwiZXhwIjoyMDgxMzY1NzA4fQ.5X3wzLn44aSsJvauwDHFJF2SuucnQaxTYxGeItj8ICA"

try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    print(f"✅ Supabase Connected: {SUPABASE_URL}")
except Exception as e:
    print(f"❌ Connection Failed: {e}")

app = FastAPI(title="HIS Multi-Payer Module")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- MODELS ---
class CoverageRule(BaseModel):
    coverage_percentage: float
    plafon_limit: float
    deductible: float

class EligibilityResponse(BaseModel):
    status: str
    patient_name: str
    nik: Optional[str]
    gender: Optional[str]
    card_number: str
    class_level: int
    sep_no: Optional[str]
    insurance_name: str
    insurance_type: str
    coverage_rules: Optional[CoverageRule] = None

class SEPRequest(BaseModel):
    card_number: str
    diagnosis_code: str
    visit_type: str = "INPATIENT"
    insurance_type: str

class SEPResponse(BaseModel):
    doc_number: str
    doc_type: str
    date: str
    visit_id: str

class GrouperRequest(BaseModel):
    doc_number: str
    icd10_code: str
    icd9_code: Optional[str] = None
    secondary_icd10: List[str] = []
    discharge_status: str = "Pulang Sehat"
    birth_weight: int = 0
    class_level: int = 1

class BillItem(BaseModel):
    name: str
    category: str
    amount: float

class SimulationResponse(BaseModel):
    simulation_type: str
    real_bill: float
    bill_items: List[BillItem]
    
    # BPJS Components
    inacbg_code: Optional[str] = None
    severity: Optional[str] = None
    tariff: Optional[float] = None
    hospital_margin: Optional[float] = None
    
    # Payment Split
    jasa_sarana: Optional[float] = None
    jasa_pelayanan: Optional[float] = None
    
    # Financial Impact
    covered_amount: Optional[float] = None
    patient_excess: Optional[float] = None
    
    # Private/Rules
    plafon_limit: Optional[float] = None
    deductible: Optional[float] = None
    description: str
    description_suffix: Optional[str] = None
    warning_flag: bool = False 

class AutoFillResponse(BaseModel):
    found: bool
    icd10: Optional[str] = None
    icd9: Optional[str] = None
    invoice_id: Optional[str] = None

# --- ENDPOINTS ---

@app.get("/api/eligibility/{card_number}", response_model=EligibilityResponse)
def check_eligibility(card_number: str):
    print(f"\n🔍 [LOOKUP] Checking Card: {card_number}")
    try:
        response = supabase.table("patient_insurances")\
            .select("*, patients(full_name, nik, gender), insurances(*)")\
            .eq("card_number", card_number)\
            .order("created_at", desc=True)\
            .limit(1)\
            .execute()

        if not response.data:
            print("   ❌ Card not found")
            raise HTTPException(status_code=404, detail=f"No. Kartu {card_number} tidak ditemukan.")

        data = response.data[0]
        
        if data.get('status') is False:
             raise HTTPException(status_code=400, detail="Status Kepesertaan TIDAK AKTIF.")
        
        patient_info = data.get("patients")
        ins_info = data.get("insurances")
        if not ins_info: ins_info = {"name": "Unknown", "type": "PRIVATE", "id": "unknown"}

        raw_type = ins_info.get('type')
        ins_name = ins_info.get('name', '').upper()
        
        normalized_type = 'PRIVATE'
        if raw_type:
            rt = raw_type.upper()
            if rt in ['GOVERNMENT', 'BPJS', 'JKN']: normalized_type = 'GOVERNMENT'
            elif rt == 'COMPANY': normalized_type = 'COMPANY'
        if 'BPJS' in ins_name: normalized_type = 'GOVERNMENT'
            
        cov_rules = None
        insurance_id = ins_info.get('id')
        
        if normalized_type != 'GOVERNMENT':
            cov_res = supabase.table("insurance_coverages").select("*").eq("insurance_id", insurance_id).limit(1).execute()
            if cov_res.data:
                rule = cov_res.data[0]
                cov_rules = CoverageRule(
                    coverage_percentage=float(rule.get('coverage_percentage', 100)),
                    plafon_limit=float(rule.get('plafon_limit', 0)),
                    deductible=float(rule.get('deductible', 0))
                )
            else:
                cov_rules = CoverageRule(coverage_percentage=100, plafon_limit=0, deductible=0)

        sep_val = data.get("sep_no")
        print(f"   ✅ FOUND: {patient_info.get('full_name')} ({normalized_type})")
        
        return EligibilityResponse(
            status="AKTIF",
            patient_name=patient_info.get('full_name', 'Unknown'),
            nik=patient_info.get('nik'),
            gender=patient_info.get("gender"),
            card_number=data.get("card_number"),
            class_level=data.get("class_id", 3),
            sep_no=sep_val,
            insurance_name=ins_info.get("name", "Unknown"),
            insurance_type=normalized_type,
            coverage_rules=cov_rules
        )
    except HTTPException as he: raise he
    except Exception as e:
        print(f"   🔥 ERROR: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/sep", response_model=SEPResponse)
def generate_document(payload: SEPRequest):
    print(f"\n📝 [DOC GEN] Generating for: {payload.card_number}")
    try:
        pat_query = supabase.table("patient_insurances").select("patient_id").eq("card_number", payload.card_number).limit(1).execute()
        if not pat_query.data: raise HTTPException(status_code=404, detail="Pasien not found")
        patient_id = pat_query.data[0]['patient_id']
        
        doc_number = ""
        doc_type = ""
        if payload.insurance_type == 'GOVERNMENT':
            doc_type = "SEP"
            doc_number = f"001R001{datetime.now().strftime('%m%d')}{str(int(datetime.now().timestamp()))[-4:]}"
        else:
            doc_type = "GL"
            timestamp_code = str(int(datetime.now().timestamp()))[-6:]
            doc_number = f"GL-{datetime.now().strftime('%Y')}-{timestamp_code}"

        doc_ref = supabase.table("doctors").select("id").eq("is_active", True).limit(1).execute()
        doctor_id = doc_ref.data[0]['id'] if doc_ref.data else None

        visit_data = {
            "patient_id": patient_id,
            "doctor_id": doctor_id,
            "visit_type": payload.visit_type,
            "status": "ADMITTED",
            "queue_number": doc_number[-4:],
            "payment_method": payload.insurance_type
        }
        
        try:
            visit_res = supabase.table("visits").insert(visit_data).execute()
            visit_id = visit_res.data[0]['id']
        except Exception:
            visit_id = "temp_visit_id"

        supabase.table("patient_insurances").update({"sep_no": doc_number}).eq("patient_id", patient_id).execute()
        print(f"   ✅ {doc_type} Created: {doc_number}")
        return SEPResponse(doc_number=doc_number, doc_type=doc_type, date=datetime.now().strftime("%Y-%m-%d"), visit_id=str(visit_id))
    except Exception as e:
        print(f"Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/grouper", response_model=SimulationResponse)
def calculate_benefits(payload: GrouperRequest):
    print(f"\n🧮 [CALC] Processing Doc: {payload.doc_number}")
    try:
        pat_res = supabase.table("patient_insurances").select("patient_id, class_id, insurances(*)").eq("sep_no", payload.doc_number).limit(1).execute()
        
        if not pat_res.data:
            raise HTTPException(status_code=404, detail="Dokumen aktif tidak ditemukan")
            
        insurance_data = pat_res.data[0]['insurances']
        patient_id = pat_res.data[0]['patient_id']
        
        raw_type = insurance_data.get('type')
        normalized_type = 'PRIVATE'
        if raw_type and raw_type.upper() in ['GOVERNMENT', 'BPJS', 'JKN', 'GOOVERNMENT']:
            normalized_type = 'GOVERNMENT'
        if 'BPJS' in insurance_data.get('name', '').upper():
            normalized_type = 'GOVERNMENT'

        # --- BILL CALCULATION LOGIC ---
        total_bill = 0.0
        bill_items = []
        
        # 1. Get Latest Invoice
        inv_res = supabase.table("invoices").select("id, total_amount").eq("patient_id", patient_id).order("created_at", desc=True).limit(1).execute()
        
        real_invoice_found = False
        
        if inv_res.data:
            invoice = inv_res.data[0]
            # 2. Get Details
            details = supabase.table("invoice_details").select("*").eq("invoice_id", invoice['id']).execute()
            
            if details.data and len(details.data) > 0:
                real_invoice_found = True
                for item in details.data:
                    cost = float(item['subtotal'])
                    total_bill += cost
                    bill_items.append(BillItem(name=item['item_name'], category=item['item_type'], amount=cost))
            
            # 3. Fallback: Use Invoice Header Total if details are missing
            elif invoice.get('total_amount') and float(invoice['total_amount']) > 0:
                real_invoice_found = True
                total_bill = float(invoice['total_amount'])
                bill_items.append(BillItem(name="Total Invoice (Header)", category="system", amount=total_bill))

        # --- PRICES FOR SIMULATION (Used if no real bill) ---
        sim_diag_price = 0.0
        sim_proc_price = 0.0
        diag_name = "Unknown"
        
        t10 = supabase.table("tariff_icd10").select("price, name").eq("code", payload.icd10_code).limit(1).execute()
        if t10.data: 
            sim_diag_price = float(t10.data[0]['price'])
            diag_name = t10.data[0]['name']
        
        if payload.icd9_code:
            t9 = supabase.table("tariff_icd9").select("price").eq("code", payload.icd9_code).limit(1).execute()
            if t9.data: sim_proc_price = float(t9.data[0]['price'])

        # --- BPJS LOGIC ---
        if normalized_type == 'GOVERNMENT':
            print("   🏥 Mode: INA-CBG (Government)")
            group_code = "UNSPECIFIED"
            
            try:
                map_res = supabase.table("ref_medical_codes").select("target_inacbg_code").eq("code", payload.icd10_code).limit(1).execute()
                if map_res.data: group_code = map_res.data[0]['target_inacbg_code']
            except Exception: pass

            severity = "I"
            desc = diag_name

            if payload.icd9_code: severity = "II"

            # Logic: Komorbiditas
            if len(payload.secondary_icd10) > 0:
                severity = "III" if severity == "II" else "II"
                sim_diag_price += (sim_diag_price * 0.2 * len(payload.secondary_icd10))

            # Logic: Neonatal
            if payload.birth_weight > 0 and payload.birth_weight < 2500:
                group_code = "P-8-XX"
                desc = f"Neonatal <2500g ({desc})"
                sim_diag_price *= 1.5

            raw_tariff = sim_diag_price + sim_proc_price
            
            class_multiplier = 1.0
            if payload.class_level == 2: class_multiplier = 1.2
            elif payload.class_level == 1: class_multiplier = 1.4
            
            final_tariff = raw_tariff * class_multiplier
            
            is_aps = payload.discharge_status == "APS"
            covered = final_tariff
            excess = 0.0
            warning = False
            desc_suffix = ""
            
            j_sarana = final_tariff * 0.56
            j_pelayanan = final_tariff * 0.44

            # Use simulated bill if real one not found
            if not real_invoice_found or total_bill == 0:
                total_bill = final_tariff * 0.85 # Assume 15% margin for demo
                bill_items.append(BillItem(name="Estimasi Biaya RS (Simulasi)", category="system", amount=total_bill))

            if is_aps:
                warning = True
                desc_suffix = " (GUGUR KLAIM - APS)"
                covered = 0.0
                excess = total_bill 
                
            return SimulationResponse(
                simulation_type="INA-CBG",
                real_bill=total_bill,
                bill_items=bill_items,
                inacbg_code=f"{group_code}-{severity}",
                description=desc + desc_suffix,
                severity=severity,
                tariff=final_tariff,
                hospital_margin=(covered - total_bill) if not is_aps else 0,
                covered_amount=covered,
                patient_excess=excess,
                jasa_sarana=j_sarana,
                jasa_pelayanan=j_pelayanan,
                warning_flag=warning,
                plafon_limit=0, deductible=0
            )
        
        # --- PRIVATE LOGIC ---
        else:
            print(f"   🛡️ Mode: Private Coverage")
            cov_res = supabase.table("insurance_coverages").select("*").eq("insurance_id", insurance_data['id']).limit(1).execute()
            coverage_pct = 100.0
            plafon = 0.0
            deductible = 0.0
            if cov_res.data:
                rule = cov_res.data[0]
                coverage_pct = float(rule.get('coverage_percentage', 100))
                plafon = float(rule.get('plafon_limit', 0))
                deductible = float(rule.get('deductible', 0))

            # DYNAMIC BILL SIMULATION (If real invoice missing)
            if not real_invoice_found or total_bill == 0:
                # Base bill from codes
                total_bill = sim_diag_price + sim_proc_price
                
                # Add extra for secondary diagnoses
                if len(payload.secondary_icd10) > 0:
                    total_bill += (sim_diag_price * 0.3 * len(payload.secondary_icd10))
                
                # Add base room/admin fee if bill is too low
                if total_bill < 500000: total_bill += 500000
                
                bill_items = [BillItem(name="Simulasi Biaya Medis (Diagnosa + Tindakan)", category="system", amount=total_bill)]

            bill_after_deductible = max(0, total_bill - deductible)
            initial_covered = bill_after_deductible * (coverage_pct / 100.0)
            final_covered = initial_covered
            if plafon > 0: final_covered = min(initial_covered, plafon)
            patient_pay = total_bill - final_covered

            return SimulationResponse(
                simulation_type="PRIVATE_COVERAGE",
                real_bill=total_bill,
                bill_items=bill_items,
                description=f"Coverage: {coverage_pct}% | Limit: {plafon:,.0f}",
                covered_amount=final_covered,
                patient_excess=patient_pay,
                plafon_limit=plafon,
                deductible=deductible,
                description_suffix=""
            )

    except Exception as e:
        print(f"Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/bill-details/{card_number}", response_model=AutoFillResponse)
def get_bill_details(card_number: str):
    print(f"\n🔍 [AUTOFILL] Checking invoice for: {card_number}")
    try:
        pat_query = supabase.table("patient_insurances").select("patient_id").eq("card_number", card_number).limit(1).execute()
        if not pat_query.data: return AutoFillResponse(found=False)
        patient_id = pat_query.data[0]['patient_id']

        inv_query = supabase.table("invoices").select("id").eq("patient_id", patient_id).order("created_at", desc=True).limit(1).execute()
        if not inv_query.data: return AutoFillResponse(found=False)
        invoice_id = inv_query.data[0]['id']

        details = supabase.table("invoice_details").select("item_type, item_code, item_name").eq("invoice_id", invoice_id).execute()
        
        icd10 = None
        icd9 = None
        
        for item in details.data:
            t = item.get("item_type")
            c = item.get("item_code")
            if t == 'icd10' and not icd10: icd10 = c
            if t == 'icd9' and not icd9: icd9 = c
        
        print(f"   ✅ Invoice Found: {invoice_id} | ICD10: {icd10} | ICD9: {icd9}")
        return AutoFillResponse(found=True, icd10=icd10, icd9=icd9, invoice_id=invoice_id)

    except Exception as e:
        print(f"Error: {e}")
        return AutoFillResponse(found=False)

@app.get("/api/references")
def get_references():
    try:
        icd10 = supabase.table("tariff_icd10").select("code, name").execute()
        icd9 = supabase.table("tariff_icd9").select("code, name").execute()
        return {"icd10": icd10.data or [], "icd9": icd9.data or []}
    except Exception as e:
        return {"icd10": [], "icd9": []}

if __name__ == "__main__":
    print("🚀 Starting Multi-Payer Backend on Port 8000...")
    uvicorn.run(app, host="0.0.0.0", port=8000)
