<font style="color: #4438c1;">why</font>
Paramedics work under time pressure, often with their hands full, and writing reports takes time away from patient care. Our system lets them simply talk. 

<span style="color: #4438c1;">what</span>
We use tech EMS already have at the scene; a work phone with mic. They speak, AI turns it into a structured summary with a diagnosis, then the report is automatically sent to the hospital. The paramedic vets the AI report, with the ability to accept, reject, or write their own comments at each step. Every decision feeds back into the AI so it improves over time. Then doctors can start treatment right away with the information they receive.

<span style="color: #4438c1;">how</span>
Backend:
We use speech-to-text AI to capture paramedic comments and populate a call report, then a OLlama-powered analysis layer diagnoses the patient, then we send the data via HL7 standard (used by Ontario hospitals). 
Frontend: Simple Flask web app.

<span style="color: #4438c1;">challenges</span>
The main challenge has been accurately filtering voice inputs. Paramedics work in noisy and chaotic environments so we need to make sure they can be heard in those conditions. Since the audio signals get garbled it's hard to make sure they transcribe accurately.

<span style="color: #4438c1;">what we're proud of</span>
A pipeline that works + proud of Claude and Gemini for coding it.
Carol: I learned that hospitals have specific data standards for their reports!
Tilo: I learned how to implement speech-to-text audio transcription with AI! 

<span style="color: #4438c1;">future</span>
In the future we'd like to integrate our project with existing EMS software. Unfortunately none of us have an Interdev tablet (most common one used by Ontarian paramedics) to test on, but if we got our hands on one we'd like to try uploading this software to it. Also, we'd like to improve our AI pipeline to be quicker and filter out unneeded audio information.
