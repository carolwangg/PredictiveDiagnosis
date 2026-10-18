/* Shared browser-microphone control for home and incident pages. */
(function (global) {
  'use strict';

  function attach(button, options) {
    var recorder = null, stream = null, chunks = [], busy = false, targetUrl = null, captureFailed = false;
    var liveTimer = null, liveInFlight = false, livePending = false, liveSequence = 0, liveApplied = 0;
    var liveFinalized = false;
    var label = button.querySelector('span');

    function setState(state) {
      button.dataset.state = state;
      button.disabled = state === 'requesting' || state === 'processing' ||
        (state === 'idle' && options.isDisabled && options.isDisabled());
      var stateLabel = options.labels && options.labels[state];
      if (label) {
        label.textContent = stateLabel || (state === 'recording' ? 'Stop recording' :
          state === 'processing' ? 'Transcribing…' :
          state === 'requesting' ? 'Connecting…' : 'Record audio');
      }
      if (stateLabel) button.setAttribute('aria-label', stateLabel);
      button.setAttribute('aria-pressed', state === 'recording' ? 'true' : 'false');
      if (options.onState) options.onState(state);
    }

    function releaseStream() {
      if (stream) stream.getTracks().forEach(function (track) { track.stop(); });
      stream = null;
      recorder = null;
    }

    function upload(blob, mimeType) {
      if (!blob.size) throw new Error('No audio was captured.');
      var extension = mimeType.indexOf('ogg') !== -1 ? '.ogg' :
        mimeType.indexOf('mp4') !== -1 ? '.mp4' :
        mimeType.indexOf('wav') !== -1 ? '.wav' : '.webm';
      var body = new FormData();
      body.append('audio', blob, 'recording' + extension);
      if (options.formFields) {
        var fields = options.formFields();
        Object.keys(fields).forEach(function (key) {
          body.append(key, fields[key]);
        });
      }
      return fetch(targetUrl, {
        method: 'POST',
        headers: { Accept: 'application/json' },
        body: body
      }).then(function (response) {
        return response.text().then(function (text) {
          var data;
          try { data = text ? JSON.parse(text) : {}; } catch (_) { data = {}; }
          if (!response.ok) throw new Error(data.error || ('HTTP ' + response.status));
          return data;
        });
      }).then(options.onResult);
    }

    function transcribeLiveSnapshot() {
      if (!options.liveUrl || liveFinalized || !chunks.length) return;
      if (liveInFlight) {
        livePending = true;
        return;
      }

      var mimeType = recorder && recorder.mimeType || 'audio/webm';
      var blob = new Blob(chunks, { type: mimeType });
      if (!blob.size) return;
      var sequence = ++liveSequence;
      var extension = mimeType.indexOf('ogg') !== -1 ? '.ogg' :
        mimeType.indexOf('mp4') !== -1 ? '.mp4' : '.webm';
      var body = new FormData();
      body.append('audio', blob, 'live-recording' + extension);
      liveInFlight = true;
      fetch(options.liveUrl(), {
        method: 'POST',
        headers: { Accept: 'application/json' },
        body: body
      }).then(function (response) {
        return response.text().then(function (text) {
          var data;
          try { data = text ? JSON.parse(text) : {}; } catch (_) { data = {}; }
          if (!response.ok) throw new Error(data.error || ('HTTP ' + response.status));
          return data;
        });
      }).then(function (data) {
        if (!liveFinalized && sequence > liveApplied && data.transcript) {
          liveApplied = sequence;
          if (options.onTranscript) options.onTranscript(data.transcript);
        }
      }).catch(function (error) {
        if (!liveFinalized && options.onLiveError) options.onLiveError(error);
      }).then(function () {
        liveInFlight = false;
        if (livePending && !liveFinalized) {
          livePending = false;
          transcribeLiveSnapshot();
        }
      });
    }

    function stopAndUpload() {
      if (!recorder || recorder.state === 'inactive') return;
      var mimeType = recorder.mimeType || 'audio/webm';
      liveFinalized = true;
      livePending = false;
      clearInterval(liveTimer);
      liveTimer = null;
      setState('processing');
      recorder.addEventListener('stop', function () {
        if (captureFailed) return;
        var blob = new Blob(chunks, { type: mimeType });
        releaseStream();
        Promise.resolve().then(function () { return upload(blob, mimeType); })
          .catch(options.onError).then(function () {
          busy = false;
          targetUrl = null;
          setState('idle');
        });
      }, { once: true });
      recorder.stop();
    }

    button.addEventListener('click', function () {
      if (busy) return;
      if (recorder && recorder.state === 'recording') {
        stopAndUpload();
        return;
      }
      busy = true;
      setState('requesting');
      Promise.resolve(options.beforeStart ? options.beforeStart() : null)
        .then(function () {
          if (!global.navigator.mediaDevices || !global.navigator.mediaDevices.getUserMedia) {
            throw new Error('This browser does not support microphone recording.');
          }
          if (!global.MediaRecorder) throw new Error('This browser does not support audio recording.');
          targetUrl = options.url();
          return global.navigator.mediaDevices.getUserMedia({ audio: true });
        })
        .then(function (audioStream) {
          stream = audioStream;
          captureFailed = false;
          var candidates = ['audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/mp4'];
          var selected = global.MediaRecorder.isTypeSupported ? candidates.filter(function (type) {
            return global.MediaRecorder.isTypeSupported(type);
          })[0] : null;
          recorder = new global.MediaRecorder(stream, selected ? { mimeType: selected } : undefined);
          chunks = [];
          recorder.addEventListener('dataavailable', function (event) {
            if (event.data && event.data.size) chunks.push(event.data);
          });
          recorder.addEventListener('error', function (event) {
            captureFailed = true;
            liveFinalized = true;
            livePending = false;
            clearInterval(liveTimer);
            liveTimer = null;
            releaseStream();
            busy = false;
            targetUrl = null;
            setState('idle');
            options.onError(event.error || new Error('Audio recording failed.'));
          }, { once: true });
          recorder.start(250);
          liveFinalized = false;
          liveApplied = 0;
          livePending = false;
          if (options.liveUrl) {
            liveTimer = global.setInterval(
              transcribeLiveSnapshot,
              options.liveIntervalMs || 3000
            );
          }
          busy = false;
          setState('recording');
        })
        .catch(function (error) {
          clearInterval(liveTimer);
          liveTimer = null;
          liveFinalized = true;
          releaseStream();
          busy = false;
          setState('idle');
          options.onError(error);
        });
    });

    global.addEventListener('beforeunload', function (event) {
      if (busy || (recorder && recorder.state === 'recording')) {
        event.preventDefault();
        event.returnValue = '';
      }
    });
  }

  global.ACRAudio = { attach: attach };
})(window);
