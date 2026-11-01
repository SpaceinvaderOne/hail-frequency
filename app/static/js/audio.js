/* HailFrequency — Audio Capture Module */

class AudioCapture {
    constructor() {
        this.mediaRecorder = null;
        this.audioChunks = [];
        this.stream = null;
        this.audioContext = null;
        this.analyser = null;
        this.isRecording = false;
        this._initialized = false;
    }

    async init() {
        // Re-initialize if stream died (e.g. screen lock on mobile)
        if (this._initialized && this.stream && !this.stream.active) {
            if (this.audioContext) {
                this.audioContext.close().catch(() => {});
            }
            this._initialized = false;
        }
        if (this._initialized) return;

        this.stream = await navigator.mediaDevices.getUserMedia({ audio: true });

        // AudioContext — use webkit prefix for iOS Safari
        const AC = window.AudioContext || window.webkitAudioContext;
        this.audioContext = new AC();

        const source = this.audioContext.createMediaStreamSource(this.stream);
        this.analyser = this.audioContext.createAnalyser();
        this.analyser.fftSize = 256;
        source.connect(this.analyser);

        this._initialized = true;
    }

    resumeContext() {
        // Must be called synchronously inside a user gesture (pointerdown)
        // for iOS Safari to allow the AudioContext to run
        if (this.audioContext && this.audioContext.state === "suspended") {
            this.audioContext.resume();
        }
    }

    start() {
        this.audioChunks = [];

        // Codec detection: iOS Safari doesn't support webm
        let mimeType;
        if (typeof MediaRecorder !== "undefined") {
            if (MediaRecorder.isTypeSupported("audio/webm;codecs=opus")) {
                mimeType = "audio/webm;codecs=opus";
            } else if (MediaRecorder.isTypeSupported("audio/webm")) {
                mimeType = "audio/webm";
            } else if (MediaRecorder.isTypeSupported("audio/mp4")) {
                mimeType = "audio/mp4";
            } else if (MediaRecorder.isTypeSupported("audio/aac")) {
                mimeType = "audio/aac";
            }
            // else: let the browser choose
        }

        const options = mimeType ? { mimeType } : undefined;
        this.mediaRecorder = new MediaRecorder(this.stream, options);

        this.mediaRecorder.ondataavailable = (e) => {
            if (e.data.size > 0) this.audioChunks.push(e.data);
        };

        this.mediaRecorder.start();
        this.isRecording = true;
    }

    stop() {
        return new Promise((resolve) => {
            if (!this.mediaRecorder || this.mediaRecorder.state === "inactive") {
                this.isRecording = false;
                resolve({ blob: new Blob([]), mimeType: "audio/webm" });
                return;
            }

            this.mediaRecorder.onstop = () => {
                const mimeType = this.mediaRecorder.mimeType || "audio/webm";
                const blob = new Blob(this.audioChunks, { type: mimeType });
                this.isRecording = false;
                resolve({ blob, mimeType });
            };
            this.mediaRecorder.stop();
        });
    }

    getFrequencyData() {
        if (!this.analyser) return new Uint8Array(0);
        const data = new Uint8Array(this.analyser.frequencyBinCount);
        this.analyser.getByteFrequencyData(data);
        return data;
    }
}
