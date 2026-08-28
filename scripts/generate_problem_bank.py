"""Generate original EE 2300 visual acceptance problems under .local/."""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".local" / "problem_bank"
FONT = "/System/Library/Fonts/Supplemental/Arial.ttf"
BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"

PROBLEMS = [
    ("01-divider", "Resistive divider", "Vin = 12 V, R1 = 2 kOhm, R2 = 4 kOhm.\nFind Vout and input current.", "12 V --[ R1 2k ]--o--[ R2 4k ]-- GND\n                    |\n                   Vout", "Vout=8 V; Iin=2 mA"),
    ("02-rc", "RC low-pass", "R = 1 kOhm, C = 1 uF.\nFind H(s), pole, and magnitude at fc.", "Vin --[ R ]--o-- Vout\n              |\n             --- C\n              |\n             GND", "H=1/(1+sRC); pole=-1000 rad/s; |H(fc)|=1/sqrt(2)"),
    ("03-rlc", "Series RLC", "R=20 Ohm, L=10 mH, C=100 uF. Output is across C.\nFind H(s) and classify damping.", "Vin --[ R ]--( L )--o\n                    |\n                   --- C -> Vout\n                    |\n                   GND", "H=1/(LCs^2+RCs+1); critically damped (zeta=1)"),
    ("04-opamp", "Inverting amplifier", "Ideal op-amp: Ri=10 kOhm, Rf=47 kOhm, Vin=0.4 V.\nFind closed-loop gain and Vout.", "Vin --[ Ri ]--o-----(-)\\\n              |        >----o Vout\n              +-[ Rf ]-------+\nGND -----------------(+) /", "gain=-4.7; Vout=-1.88 V"),
    ("05-slew", "Op-amp limits", "Noise gain=10, GBW=1 MHz, slew rate=0.5 V/us.\nFor a 10 V peak sine at 10 kHz, identify the limiting effect.", "        10 V peak\n    ~~~ /\\ /\\ ~~~   10 kHz", "small-signal BW=100 kHz; required SR=0.628 V/us; slew limited"),
    ("06-diode", "Diode bias", "5 V source, 1 kOhm resistor, silicon diode to ground.\nUse Is=1e-14 A, n=1, VT=25.85 mV. Find the diode operating voltage.", "+5 V --[ 1k ]--|>|-- GND\n                 Vd", "Vd approximately 0.693 V"),
    ("07-bjt", "BJT bias and gain", "An NPN common-emitter stage is biased at IC=1.0 mA.\nRC=1 kOhm, VCC=5 V, VT=25 mV. Find VC and midband gain -gm*RC.", "+5 --[ RC ]--o collector\n             |\n            |\\  NPN\nAC in ------| >\n            |/\n             |\n            GND", "VC=4.0 V; gm=40 mS; gain=-40 V/V; forward active"),
    ("08-mos", "MOS common source", "NMOS level 1: VTO=1 V, KP=1 mA/V^2, W/L=1, lambda=0.\nVG=2 V, RD=1 kOhm, VDD=5 V. Use ID=KP(VGS-VTO)^2/2.", "+5 --[ RD ]--o drain\n             |\n2 V --------|  NMOS\n             |\n            GND\nFind ID, VD, and -gm*RD.", "ID=0.5 mA; VD=4.5 V; gain=-1"),
    ("09-feedback", "Feedback stability", "Loop transfer L(s)=10/[s(s+1)].\nFind gain crossover and phase margin. Is closed-loop feedback stable?", "          +          10\nR(s) ---> (o) ----> ------- ----> Y(s)\n          - ^       s(s+1)        |\n            |______________________|", "wgc approximately 3.084 rad/s; PM approximately 17.96 deg; stable"),
    ("10-adc", "Two-bit ADC", "Range 0 to 1 V; transitions are 0.25, 0.25, 0.75 V.\nUsing ideal endpoints and transition INL, find INL/DNL and missing codes.", "Vin ---> [ 2-bit ADC ] ---> b1 b0\n          0 ... 1 V", "missing code 1; one bin DNL=-1 LSB"),
    ("11-spectrum", "Spectral distortion", "A waveform has a 1.0 V peak fundamental and 0.10 V peak\nsecond harmonic; other harmonics are zero. Find THD and THD(dB).", "amp\n1.0 |  |\n0.1 |     |\n    +--f--2f----------> frequency", "THD=10%; THD=-20 dB"),
    ("12-comparator", "Comparator", "An ideal rail-limited comparator has +/-5 V rails.\nFind Vout for Vin=-1 mV, 0 V, and +1 mV; no zero-input convention.", "Vin ---> (+)\\\n            >---- Vout\n0 V ----> (-)/     rails +/-5 V", "-5 V, undefined at exactly 0 V, +5 V"),
    ("13-rl", "RL high-pass", "R=100 Ohm and L=10 mH are in series; output is across L.\nFind H(s), its zero and pole, and the -3 dB frequency.", "Vin --[ R ]--( L )-- GND\n              |< Vout >|", "H=sL/(R+sL)=s/(s+10000); zero=0; pole=-10000 rad/s; fc=1591.55 Hz"),
    ("14-finite-opamp", "Finite-gain op-amp", "Non-inverting amplifier: Rg=1 kOhm, Rf=9 kOhm, open-loop A=1000.\nFind the exact closed-loop gain and compare it with the ideal value.", "Vin -->(+)\\\n          >----o Vout\n      (-)/     |\n       o--[9k]-+\n       |\n      [1k]\n       | GND", "gain=A*(Rg+Rf)/(A*Rg+Rg+Rf)=10000/1010=9.90099; ideal=10"),
    ("15-rectifier", "Half-wave rectifier", "A 5 V peak, 1 kHz sine drives an ideal diode and 1 kOhm load.\nState vout during positive/negative half-cycles and its average DC value.", "~ Vin --|>|--o Vout\n              |\n             [1k]\n              | GND", "positive: vout=vin; negative: 0; average=Vp/pi=1.59155 V"),
    ("16-zener", "Zener regulator", "A 10 V supply feeds 1 kOhm then a 5.1 V ideal zener to ground.\nWith no load, find Vout and zener current.", "+10 --[1k]--o Vout\n             |\n          [Z 5.1V]\n             | GND", "Vout=5.1 V; Iz=4.9 mA"),
    ("17-emitter-follower", "Emitter follower", "An NPN emitter follower is biased at IC=2 mA with beta=100.\nVT=25 mV and load RL=1 kOhm. Ignore ro; find gm, rpi, and gain.", "Vin --> base >|-- emitter --o Vout\n               |             |\n              collector     [1k]\n               +V            | GND", "gm=80 mS; rpi=1.25 kOhm; gain=(beta+1)RL/(rpi+(beta+1)RL)=0.98778"),
    ("18-mos-region", "MOS region check", "NMOS: VTO=1 V, KP=2 mA/V^2, W/L=1, lambda=0.\nVGS=3 V and VDS=0.5 V. Identify region and find ID.", "drain 0.5V\n    |\n3V--| NMOS\n    |\n   GND", "triode; ID=KP*((Vov)*VDS-VDS^2/2)=1.75 mA"),
    ("19-oscillator", "Relaxation oscillator", "A Schmitt RC oscillator has thresholds +/-2 V, rails +/-5 V,\nand RC=1 ms. Find the oscillation period and frequency.", "+/-5 V Schmitt ---> [ R ]--o vc\n      ^                     |\n      |                    --- C\n      +---------------------|", "T=2RC*ln((5+2)/(5-2))=1.6946 ms; f=590.0 Hz"),
    ("20-dac", "Three-bit DAC", "An ideal 3-bit unipolar DAC spans 0 to 8 V using 1 V/LSB.\nFind outputs for codes 000, 011, 101, and 111.", "b2 b1 b0 ---> [ DAC ] ---> Vout", "0 V, 3 V, 5 V, 7 V"),
    ("21-alias", "Sampling and aliasing", "A 1.3 kHz sinusoid is sampled at 1.0 kS/s.\nWhat baseband alias frequency appears between 0 and fs/2?", "1.3 kHz ---> [ sample @ 1 kS/s ] ---> ?", "300 Hz"),
    ("22-fourier", "Square-wave spectrum", "A symmetric +/-2 V, 1 kHz square wave has zero DC.\nFind peak amplitudes of its first and third sine harmonics.", " +2  __    __\n     |  |  |  |\n -2 _|  |__|  |__", "first=4A/pi=8/pi=2.54648 V peak; third=8/(3pi)=0.848826 V peak"),
    ("23-loading", "Instrument loading", "A 10 V Thevenin source with Rth=1 MOhm is measured by a 10 MOhm scope.\nFind the indicated voltage and percent loading error.", "10 V --[1 M]--o scope input\n               |\n             [10 M]\n               | GND", "Vmeas=10*10/11=9.09091 V; error=-9.0909%"),
    ("24-transimpedance", "Transimpedance amplifier", "An ideal op-amp has its + input grounded, input current 20 uA\ninto the summing node, and Rf=100 kOhm. Find Vout.", "20 uA --->o----(-)\\\n           |       >---o Vout\n           +-[100k]----+\nGND ------------(+) /", "Vout=-Iin*Rf=-2 V"),
]


def wrapped(draw, xy, text, font, fill, width=62):
    words, lines, line = text.split(), [], ""
    for word in words:
        candidate = (line + " " + word).strip()
        if len(candidate) > width:
            lines.append(line); line = word
        else: line = candidate
    if line: lines.append(line)
    draw.multiline_text(xy, "\n".join(lines), font=font, fill=fill, spacing=9)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    title = ImageFont.truetype(BOLD, 42); body = ImageFont.truetype(FONT, 28)
    mono = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 23)
    small = ImageFont.truetype(FONT, 17)
    manifest = []
    for index, (slug, topic, question, sketch, expected) in enumerate(PROBLEMS, 1):
        image = Image.new("RGB", (1200, 760), "#fffaf0"); draw = ImageDraw.Draw(image)
        for x in range(0, 1200, 28): draw.line((x, 0, x, 760), fill="#d9e9ed", width=1)
        for y in range(0, 760, 28): draw.line((0, y, 1200, y), fill="#d9e9ed", width=1)
        draw.rounded_rectangle((35, 30, 1165, 720), radius=22, outline="#244b68", width=4, fill="#fffaf0")
        draw.text((70, 63), f"EE 2300  /  visual check {index:02d}", font=small, fill="#7a8c96")
        draw.text((70, 105), topic, font=title, fill="#183446")
        wrapped(draw, (72, 180), question, body, "#244b68")
        draw.rounded_rectangle((95, 360, 1105, 645), radius=15, outline="#8fb8cc", width=3, fill="#f7f0df")
        draw.multiline_text((135, 405), sketch, font=mono, fill="#183446", spacing=12)
        draw.text((960, 675), "show your work  ~", font=small, fill="#b85c52")
        path = OUT / f"{slug}.png"; image.save(path)
        manifest.append({"id": slug, "topic": topic, "image": str(path), "expected": expected})
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps({"generated": len(manifest), "directory": str(OUT)}))


if __name__ == "__main__": main()
