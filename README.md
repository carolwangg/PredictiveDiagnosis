
Paramedics work under immense time pressure, often with their hands full and surrounded by noise. Writing reports takes valuable time away from patient care.
$\color{blue}{\text{Our goal: let paramedics talk, not type.}}$
```text
 o_______________}o{
 |              |   \
 |    911       |____\_____
 | _____        |    |_o__ |
 [/ ___ \       |   / ___ \|
[]_/.-.\_\______|__/_/.-.\_[]
   |(O)|             |(O)|
    '-'               '-'
```

## What is this?
We use tech EMS already have at the scene: a work phone with mic and ECPR report tablet. Then we turn
it into a system that reduces paperwork, speeds up patient handoffs, and saves time.

Paramedic speaks -> AI generates a report -> paramedics review the report -> hospital receives the information. At each step, paramedics have the option to clear or rewrite the AI-filled responses. At the end, doctors can receive information sooner and prepare for incoming patients.

## How we built it
**Backend**

- Speech-to-text AI captures paramedic comments.
- DeepFilterNet enhances audio in noisy environments.
- An Ollama-powered language model analyzes the transcript and suggests a diagnosis.
- HL7-based data exchange supports interoperability with hospital systems.

**Frontend**

A simple Flask web application provides the interface for speech capture, report review, and corrections.

**Technology stack:** Python, Flask, DeepFilterNet, OpenAI Fast Whisper, Ollama Qwen 2.5.

<img width="1172" height="841" alt="EMMS Hack the Valley 10" src="https://github.com/user-attachments/assets/07b57cc4-af94-4312-92c1-544fb1e7eb2d" />

## Future implementations
Our biggest challenge has been accurately capturing speech in noisy, chaotic environments. Sirens, background conversations, and environmental noise can distort audio and cause transcription errors. We have been working to improve audio filtering and transcription accuracy under these conditions.
Our next steps are to:
- **Improve filtering:** Remove irrelevant background noise while preserving important speech.
- - **Improve reliability:** Strengthen transcription and AI-generated suggestions while keeping paramedics in control.
- **Integrate with EMS software:** Explore deployment on Interdev tablets commonly used by Ontario paramedics. We have not yet had access to a device for compatibility testing.
- **Reduce latency:** Make report generation faster<img width="1172" height="841" alt="EMMS Hack the Valley 10" src="https://github.com/user-attachments/assets/03068cc7-7d7e-4c26-bead-6ed57e4d1700" />

