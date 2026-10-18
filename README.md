$${\color{blue}why} $$

Paramedics work under time pressure, often with their hands full, and writing reports takes time away from patient care. Our system lets them simply talk. 

$${\color{blue}what} $$

We use tech EMS already have at the scene; a work phone with mic. They$$\Huge{\color{blue}{\texttt{EMS VOICE-TO-REPORT AI}}}$$

$$\texttt{[+] LESS PAPERWORK \quad [+] FASTER HANDOFFS}$$

$$\texttt{[+] MORE TIME FOR PATIENT CARE}$$

---

$$\Large{\color{blue}{\texttt{[ WHY ]}}}$$

```text
       O
      /|\
      / \
   PARAMEDIC
```

Paramedics work under immense time pressure, often with their hands full and surrounded by noise. Writing reports takes valuable time away from patient care.

**Our goal: let paramedics talk, not type.**

---

$$\Large{\color{blue}{\texttt{[ WHAT ]}}}$$

```text
  +-------------+       +-------------+
  | PARAMEDIC   | ----> | AI REPORT   |
  | SPEAKS      |       | GENERATED   |
  +-------------+       +-------------+
                                |
                                v
  +-------------+       +-------------+
  | HOSPITAL    | <---- | EMS REVIEW  |
  | RECEIVES    |       | + APPROVAL  |
  +-------------+       +-------------+
```

We use technology paramedics already have: a work phone with a microphone.

1. **Speak:** Paramedics dictate their observations.
2. **Transcribe:** AI converts speech into text.
3. **Analyze:** An AI model generates a structured report and suggested diagnosis.
4. **Review:** Paramedics can accept, reject, or add comments at each step.
5. **Transmit:** The finalized report is sent to the hospital.
6. **Improve:** Feedback helps guide future improvements to the system.

Doctors can receive information sooner and prepare for incoming patients.

---

$$\Large{\color{blue}{\texttt{[ HOW ]}}}$$

$$\texttt{AUDIO} \rightarrow \texttt{ENHANCEMENT} \rightarrow \texttt{TRANSCRIPTION}$$

$$\texttt{\downarrow}$$

$$\texttt{STRUCTURED REPORT} \rightarrow \texttt{AI ANALYSIS}$$

$$\texttt{\downarrow}$$

$$\texttt{PARAMEDIC REVIEW} \rightarrow \texttt{HL7 DATA EXCHANGE}$$

**Backend**

- Speech-to-text AI captures paramedic comments.
- DeepFilterNet enhances audio in noisy environments.
- An Ollama-powered language model analyzes the transcript and suggests a diagnosis.
- HL7-based data exchange supports interoperability with hospital systems.

**Frontend**

A simple Flask web application provides the interface for speech capture, report review, and corrections.

**Technology stack:** Python, Flask, DeepFilterNet, speech-to-text AI, Ollama, HL7.

---

$$\Large{\color{blue}{\texttt{[ CHALLENGES ]}}}$$

```text
   ~~~ SIRENS ~~~
       \  |  /
        \ | /
         \|/
       [VOICE]
          |
          v
     [TRANSCRIPT]
```

Our biggest challenge has been accurately capturing speech in noisy, chaotic environments.

Sirens, background conversations, and environmental noise can distort audio and cause transcription errors. We have been working to improve audio filtering and transcription accuracy under these conditions.

---

$$\Large{\color{blue}{\texttt{[ WHAT WE'RE PROUD OF ]}}}$$

```text
  [ AUDIO ] -> [ AI ] -> [ REPORT ] -> [ HOSPITAL ]
                   WORKING PIPELINE
```

We are proud to have built a working end-to-end pipeline connecting speech capture, audio enhancement, transcription, AI-assisted analysis, and structured reporting.

We also made effective use of Claude and Gemini as coding assistants throughout development.

**Carol:** Learned that hospitals rely on specific data standards for exchanging patient reports.

**Tilo:** Learned how to implement AI-powered speech-to-text transcription.

---

$$\Large{\color{blue}{\texttt{[ FUTURE ]}}}$$

```text
  +-----------------------+
  | EXISTING EMS SOFTWARE |
  +-----------------------+
             |
             v
  +-----------------------+
  | FASTER AI PROCESSING  |
  +-----------------------+
             |
             v
  +-----------------------+
  | BETTER NOISE FILTERS  |
  +-----------------------+
             |
             v
  +-----------------------+
  | CLINICAL RELIABILITY  |
  +-----------------------+
```

Our next steps are to:

- **Integrate with EMS software:** Explore deployment on Interdev tablets commonly used by Ontario paramedics. We have not yet had access to a device for compatibility testing.
- **Reduce latency:** Make report generation faster.
- **Improve filtering:** Remove irrelevant background noise while preserving important speech.
- **Improve reliability:** Strengthen transcription and AI-generated suggestions while keeping paramedics in control.

$$\Large{\color{blue}{\texttt{OUR MISSION}}}$$

$$\texttt{MORE TIME FOR PATIENTS.}$$

$$\texttt{LESS TIME ON PAPERWORK.}$$ speak, AI turns it into a structured summary with a diagnosis, then the report is automatically sent to the hospital. The paramedic vets the AI report, with the ability to accept, reject, or write their own comments at each step. Every decision feeds back into the AI so it improves over time. Then doctors can start treatment right away with the information they receive.

$${\color{blue}how} $$

Backend:
We use speech-to-text AI to capture paramedic comments and populate a call report, then a OLlama-powered analysis layer diagnoses the patient, then we send the data via HL7 standard (used by Ontario hospitals). 
Frontend: Simple Flask web app.

$${\color{blue}challenges} $$

The main challenge has been accurately filtering voice inputs. Paramedics work in noisy and chaotic environments so we need to make sure they can be heard in those conditions. Since the audio signals get garbled it's hard to make sure they transcribe accurately.

$${\color{blue}what} \space {\color{blue}we're} \space {\color{blue}proud} \space {\color{blue}of} $$

A pipeline that works + proud of Claude and Gemini for coding it.
Carol: I learned that hospitals have specific data standards for their reports!
Tilo: I learned how to implement speech-to-text audio transcription with AI! 

$${\color{blue}future} $$

In the future we'd like to integrate our project with existing EMS software. Unfortunately none of us have an Interdev tablet (most common one used by Ontarian paramedics) to test on, but if we got our hands on one we'd like to try uploading this software to it. Also, we'd like to improve our AI pipeline to be quicker and filter out unneeded audio information.
