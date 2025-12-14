from typing import List, Optional
from datetime import datetime
from uuid import uuid4
from decimal import Decimal
from pydantic import BaseModel, Field

# --- DATA MODELS (Based on Backend_Variables_Schema.md) ---

class PatientIdentity(BaseModel):
    patient_id: str
    full_name: str
    nik: str
    mr_no: str

class CoverageCheckRequest(BaseModel):
    bpjs_card_no: str = Field(..., description="noKartu")
    nik: str = Field(..., description="noNIK - Alternative lookup")
    visit_date: str = Field(..., description="tglSep - YYYY-MM-DD")

class CoverageResponse(BaseModel):
    is_active: bool
    bpjs_card_no: str
    bpjs_class_id: int # 1, 2, 3
    patient_data: PatientIdentity
    message: str

class SEPRequest(BaseModel):
    """Request to generate Surat Eligibilitas Peserta"""
    bpjs_card_no: str
    referral_no: str
    visit_type: int # 1=Inpatient, 2=Outpatient
    policlinic_code: str # e.g., 'INT' for Internal Medicine
    diagnosis_code: str # Initial ICD-10

class SEPResponse(BaseModel):
    sep_no: str # The generated SEP ID
    sep_date: str
    faskes_rujukan: str
    provider_name: str

class InacbgSimulationRequest(BaseModel):
    """Payload for the Grouper Simulation"""
    sep_no: str
    bpjs_class_id: int
    primary_diagnosis: str # ICD-10
    secondary_diagnoses: List[str] = []
    procedures: List[str] = [] # ICD-9-CM
    birth_date: str
    gender: str # 'L' or 'P'

class InacbgSimulationResponse(BaseModel):
    inacbg_code: str # e.g., J-4-10-I
    description: str
    severity_level: int # I, II, III
    inacbg_tariff: Decimal # The amount BPJS pays
    special_drug_code: Optional[str] = None
    special_prosthesis_code: Optional[str] = None
    tariff_topup: Decimal = Decimal(0)
    total_approved: Decimal

# --- MOCK LOGIC SERVICE ---

class BPJSService:
    
    # Mock Data: ICD-10 to Tariff Mapping (Simplified for MVP)
    # In real life, this is a complex C++ binary provided by MoH
    TARIFF_TABLE = {
        "A01.0": {"code": "A-4-10", "base_tariff": 4500000, "desc": "Typhoid Fever"},
        "I10":   {"code": "I-4-10", "base_tariff": 3200000, "desc": "Essential Hypertension"},
        "E11.9": {"code": "E-4-10", "base_tariff": 5100000, "desc": "Type 2 Diabetes Mellitus"},
        "J06.9": {"code": "J-4-10", "base_tariff": 1500000, "desc": "Acute URI"},
        "K35.8": {"code": "K-4-10", "base_tariff": 8500000, "desc": "Acute Appendicitis"}
    }

    def check_eligibility(self, req: CoverageCheckRequest) -> CoverageResponse:
        """
        Simulates V-Claim participant inquiry.
        Logic: 
        - Even numbers = Active Class 1
        - Odd numbers = Active Class 3
        - Ends with 0 = Inactive/Arrears
        """
        is_active = True
        bpjs_class = 3
        msg = "Peserta Aktif"

        if req.bpjs_card_no.endswith("0"):
            is_active = False
            msg = "Peserta Tidak Aktif (Tunggakan Iuran)"
        elif int(req.bpjs_card_no[-1]) % 2 == 0:
            bpjs_class = 1
        
        # Mock Patient Return
        return CoverageResponse(
            is_active=is_active,
            bpjs_card_no=req.bpjs_card_no,
            bpjs_class_id=bpjs_class,
            patient_data=PatientIdentity(
                patient_id=str(uuid4()),
                full_name="Budi Santoso (Simulasi)",
                nik=req.nik,
                mr_no="00-12-34-56"
            ),
            message=msg
        )

    def generate_sep(self, req: SEPRequest) -> SEPResponse:
        """
        Simulates creating an SEP. 
        Format: 1301R001 + MMDDYY + 5 Digits
        """
        today_str = datetime.now().strftime("%m%d%y")
        random_suffix = str(uuid4().int)[:5]
        sep_number = f"1301R001{today_str}V{random_suffix}"
        
        return SEPResponse(
            sep_no=sep_number,
            sep_date=datetime.now().strftime("%Y-%m-%d"),
            faskes_rujukan=f"Puskesmas {req.referral_no[:4]}",
            provider_name="RS Sehat Sentosa"
        )

    def simulate_grouper(self, req: InacbgSimulationRequest) -> InacbgSimulationResponse:
        """
        Simulates INA-CBG Grouping Logic.
        1. Look up primary diagnosis.
        2. Adjust for class.
        3. Adjust for severity (secondary diagnoses count).
        """
        
        # 1. Base Grouping
        diag_data = self.TARIFF_TABLE.get(req.primary_diagnosis.upper())
        if not diag_data:
            # Fallback for unknown codes
            diag_data = {"code": "Z-9-99", "base_tariff": 1000000, "desc": "Unspecified Group"}

        base_tariff = Decimal(diag_data["base_tariff"])
        code_base = diag_data["code"]

        # 2. Severity Logic (Mock: More secondary diag = higher severity)
        severity = 1
        if len(req.secondary_diagnoses) >= 2:
            severity = 3
        elif len(req.secondary_diagnoses) == 1:
            severity = 2
            
        full_code = f"{code_base}-{ 'I' * severity }" # e.g., I-4-10-III

        # 3. Class Adjustment (Class 1 is more expensive than Class 3)
        # Class 3 = Base
        # Class 2 = Base * 1.2
        # Class 1 = Base * 1.4
        class_multiplier = Decimal(1.0)
        if req.bpjs_class_id == 2:
            class_multiplier = Decimal(1.2)
        elif req.bpjs_class_id == 1:
            class_multiplier = Decimal(1.4)
            
        # 4. Procedure Adjustment (Mock: Surgery adds flat fee)
        proc_fee = Decimal(0)
        if req.procedures:
            proc_fee = Decimal(2500000) * len(req.procedures)

        total_tariff = (base_tariff * class_multiplier) + (proc_fee * Decimal(severity))

        return InacbgSimulationResponse(
            inacbg_code=full_code,
            description=diag_data["desc"],
            severity_level=severity,
            inacbg_tariff=total_tariff,
            total_approved=total_tariff
        )

# --- EXAMPLE USAGE (For testing) ---

if __name__ == "__main__":
    service = BPJSService()
    
    # 1. Check Coverage
    coverage = service.check_eligibility(CoverageCheckRequest(
        bpjs_card_no="000123456788", 
        nik="3515123456780001", 
        visit_date="2025-12-10"
    ))
    print(f"Coverage: {coverage.message}, Class: {coverage.bpjs_class_id}")
    
    # 2. Simulate Claim
    simulation = service.simulate_grouper(InacbgSimulationRequest(
        sep_no="123",
        bpjs_class_id=coverage.bpjs_class_id,
        primary_diagnosis="A01.0", # Typhoid
        secondary_diagnoses=["E11.9"], # Diabetes (Comorbidity)
        birth_date="1980-01-01",
        gender="L"
    ))
    
    print(f"INA-CBG Code: {simulation.inacbg_code}")
    print(f"Total Approved: Rp {simulation.total_approved:,.2f}")