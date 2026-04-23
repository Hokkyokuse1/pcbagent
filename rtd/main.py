import sys
sys.path.insert(0, ".")

from skidl import *

# Target KiCad 5 backend
set_default_tool(KICAD5)


# ──── CRITICAL FIX 1: Set footprint paths FIRST ───────────────────────────────
footprint_search_paths[KICAD5] = [
    "ESP32-S3-WROOM-2.pretty",        # Direct path to custom ESP32 footprint
    "kicad-libraries/footprints/Espressif.pretty",  # Espressif footprints
    ".",                               # Project root
    "/usr/share/kicad/footprints",
]

lib_search_paths[KICAD5] = [
    ".",                         # project root for sym-lib-table and .lib
    "/usr/share/kicad/symbols",
    "/usr/share/kicad/library",
]


# ── Global nets ──────────────────────────────────────────────────────────
vcc       = Net("VCC");       vcc.drive = POWER
gnd       = Net("GND");       gnd.drive = POWER
vref      = Net("VREF")
rtd_p     = Net("RTD+")
rtd_n     = Net("RTD-")
amp_out   = Net("AMP_OUT")
adc_clk   = Net("ADC_CLK")   
adc_data  = Net("ADC_DATA")  
adc_cs    = Net("ADC_CS")     
esp_rst   = Net("ESP_RST")
# FIX: Remove these global nets and define them locally where needed
# comp1     = Net("COMP1")
# comp2     = Net("COMP2")

# ── 1) Voltage-reference divider ───────────────────────────────────────────
@subcircuit
def voltage_reference(vcc, gnd, vref):
    r1 = Part("Device","R",value="10k", footprint="Resistor_SMD:R_0805_2012Metric")
    r2 = Part("Device","R",value="10k", footprint="Resistor_SMD:R_0805_2012Metric")
    r1[1] += vcc;  r1[2] += vref
    r2[1] += vref; r2[2] += gnd

# ── 2) 4-wire RTD current source ────────────────────────────────────────────
@subcircuit
def rtd_current_source(vref, vcc, gnd, rtd_p, rtd_n):
    lm = Part("Amplifier_Operational","LM324",
              footprint="Package_SO:SOIC-14_3.9x8.7mm_P1.27mm")
    lm[4] += vcc; lm[11] += gnd

    tr  = Part("Transistor_BJT","2N3904",
               footprint="Package_TO_SOT_THT:TO-92_Inline")
    rs  = Part("Device","R",value="100",footprint="Resistor_SMD:R_0805_2012Metric")
    jrt = Part("Connector_Generic","Conn_01x04",
           footprint="Connector_PinHeader_1x04_P2.54mm_Vertical:PinHeader_1x04_P2.54mm_Vertical",ref="JRTD")

    # Current-source loop
    lm[3] += vref; lm[2] += lm[1]; lm[1] += tr["B"]
    tr["C"] += jrt[1]; tr["E"] += rs[1]; rs[2] += gnd; jrt[4] += gnd

    # Both Pt100 & Pt1000 sense resistors
    pt100  = Part("Device","R",value="PT100", footprint="Resistor_SMD:R_0805_2012Metric")
    pt1000 = Part("Device","R",value="PT1000",footprint="Resistor_SMD:R_0805_2012Metric")
    pt100[1] += jrt[2];  pt100[2] += jrt[3]
    pt1000[1] += jrt[2]; pt1000[2] += jrt[3]

    rtd_p += jrt[2]; rtd_n += jrt[3]

    # Terminate unused amplifiers
    for (p,m,o) in [(5,6,7),(10,9,8),(12,13,14)]:
        lm[p] += gnd; lm[m] += lm[o]

# ── 3) Instrumentation amp ─────────────────────────────────────────────────
@subcircuit
def instrumentation_amp(rtd_p, rtd_n, vcc, gnd, amp_out):
    lm2 = Part("Amplifier_Operational","LM324",
               footprint="Package_SO:SOIC-14_3.9x8.7mm_P1.27mm")
    lm2[4] += vcc; lm2[11] += gnd

    r1 = Part("Device","R",value="10k", footprint="Resistor_SMD:R_0805_2012Metric")
    r2 = Part("Device","R",value="10k", footprint="Resistor_SMD:R_0805_2012Metric")
    rg = Part("Device","R",value="1k",  footprint="Resistor_SMD:R_0805_2012Metric")

    lm2[5] += rtd_p;  lm2[6] += lm2[7]
    lm2[10] += rtd_n; lm2[9] += lm2[8]
    lm2[12] += r1[1]; r1[2] += lm2[7]
    lm2[13] += r2[1]; r2[2] += lm2[8]
    rg[1] += r1[2]; rg[2] += r2[2]

    # Terminate unused section
    lm2[3] += gnd; lm2[2] += lm2[1]

    for R in (r1,r2,rg):
        R.do_erc=False

    amp_out += lm2[14]

# ── 4) Custom SPI-style ADC front-end ───────────────────────────────────────
@subcircuit
def custom_spi_adc(analog_in, vcc, gnd, adc_clk, adc_data, adc_cs, esp_rst):
    inst = Part("Amplifier_Instrumentation","AD8421",
                footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm")
    
    # Correct power connections
    inst[8] += vcc    # Vs+ 
    inst[5] += gnd    # Vs-
    inst[6] += gnd    # Reference
    inst[2] += gnd    # Connect pin 2 (RG) if required.  
    inst[4] += gnd

    # Gain resistor connection
    rg = Part("Device","R",value="1k",footprint="Resistor_SMD:R_0805_2012Metric")
    rg[1] += inst[1]  # RG1
    rg[2] += inst[8]  # RG2

    # Input filter
    rf = Part("Device","R",value="100", footprint="Resistor_SMD:R_0805_2012Metric")
    cf = Part("Device","C",value="100nF",footprint="Capacitor_SMD:C_0402_1005Metric")
    rf[1] += analog_in; rf[2] += inst[3]
    cf[1] += inst[3]; cf[2] += gnd

    # Comparator configuration
    comp = Part("Comparator","LM393", footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm")
    comp[8] += vcc; comp[4] += gnd
    
    # FIX: Create intermediate nets for the comparator outputs
    comp_out1 = Net("COMP_OUT1")
    comp_out2 = Net("COMP_OUT2")
    
    # Configure used comparator channel
    comp[3] += inst[3]  # +IN
    comp[2] += inst[7]  # -IN
    comp[7] += comp_out2  # Output (now uses local net)
    
    # Configure unused comparator channel
    comp[5] += gnd      # +IN
    comp[6] += gnd      # -IN
    comp[1] += comp_out1  # Output (now uses local net instead of NC)

    # Pull-up resistors
    r_pull1 = Part("Device", "R", value="10k", footprint="Resistor_SMD:R_0805_2012Metric")  
    r_pull2 = Part("Device", "R", value="10k", footprint="Resistor_SMD:R_0805_2012Metric")  
    r_pull1[1] += vcc; r_pull1[2] += comp_out1  # COMP1 pull-up  
    r_pull2[1] += vcc; r_pull2[2] += comp_out2  # COMP2 pull-up
    
    # FIX: Add buffer resistors between the comp outputs and the connection to ESP32
    # This helps resolve the pin conflict between OPEN-COLLECTOR and BIDIRECTIONAL
    buf1 = Part("Device", "R", value="1k", footprint="Resistor_SMD:R_0805_2012Metric")  
    buf2 = Part("Device", "R", value="1k", footprint="Resistor_SMD:R_0805_2012Metric")  
    buf1[1] += comp_out1; buf1[2] += adc_cs
    buf2[1] += comp_out2; buf2[2] += adc_data
    
    # Reset transistor
    tr = Part("Transistor_BJT","2N3904", footprint="Package_TO_SOT_THT:TO-92_Inline")
    tr["C"] += inst[1]; tr["E"] += gnd; esp_rst += tr["B"]
    
    # Add pullup to ESP_RST
    rst_pull = Part("Device","R",value="10k",footprint="Resistor_SMD:R_0402_1005Metric")
    rst_pull[1] += vcc; rst_pull[2] += esp_rst
    
    adc_clk += inst[7]

# ── 5) ESP32 interface ─────────────────────────────────────────────────────
@subcircuit
def esp32_interface(adc_data, adc_cs, adc_clk, esp_rst, vcc, gnd):
    esp = Part(
        "ESP32-S3-WROOM-2",        
        "ESP32-S3-WROOM-2-N16R8V",  # ← exact symbol name from the .lib
        footprint="ESP32-S3-WROOM-2:ESP32-S3-WROOM-2"
    )
    
    # Power connections
    esp["GND"] += gnd
    esp["3V3"] += vcc
    
    # EN pin handling with pullup
    en_pull = Part("Device","R",value="10k",footprint="Resistor_SMD:R_0805_2012Metric")
    en_pull[1] += vcc
    en_pull[2] += esp["EN"]
    
    # Signal connections
    esp[15] += adc_data
    esp[18] += adc_cs
    esp[17] += adc_clk
    esp[16] += esp_rst
    
    # Explicitly mark unused pins
    unused_pins = [4,5,6,7,8,9,10,11,12,13,14,
                   19,20,21,22,23,24,25,26,27,
                   31,32,33,34,35,36,37,38,39]
    for pin in unused_pins:
        esp[pin] += NC

# ── Top-level assembly ─────────────────────────────────────────────────────
if __name__=="__main__": 
    # Power flags
    pf1 = Part("power","PWR_FLAG", footprint="Power:Flag")
    pf2 = Part("power","PWR_FLAG", footprint="Power:Flag")
    vcc += pf1[1]; gnd += pf2[1]

    # Build flow
    voltage_reference(vcc, gnd, vref)
    rtd_current_source(vref, vcc, gnd, rtd_p, rtd_n)
    instrumentation_amp(rtd_p, rtd_n, vcc, gnd, amp_out)
    custom_spi_adc(amp_out, vcc, gnd, adc_clk, adc_data, adc_cs, esp_rst)
    esp32_interface(adc_data, adc_cs, adc_clk, esp_rst, vcc, gnd)

    # Decoupling capacitors
    for p in default_circuit.parts:
        if p.name in ["LM324","AD8421","LM393","ESP32-S3-WROOM-2"]:
            c = Part("Device","C",value="100nF", footprint="Capacitor_SMD:C_0402_1005Metric")
            c[1] += vcc; c[2] += gnd

    # Final outputs
    ERC()
    generate_netlist()
    generate_svg()
    
    # FIX: Try to make schematic generation more robust
    generate_schematic(
        filepath='.',
        title="RTD Driver Prototype",
        author="Paul Munyao",
        flatness=0.5,  # Increase flatness to help with routing
        retries=20     # Increase retries
    )
