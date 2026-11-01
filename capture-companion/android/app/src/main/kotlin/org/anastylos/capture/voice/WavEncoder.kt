package org.anastylos.capture.voice

import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * Mono 16-bit PCM WAV, the only format the workstation transcriber
 * accepts (16 or 24 kHz, at most 10 min / 32 MB; `transcribe.py`
 * `check_wav`). Bytes stay in memory; nothing is written to storage.
 */
object WavEncoder {
    const val MAX_BYTES = 32 * 1024 * 1024
    private const val HEADER_BYTES = 44

    fun encode(samples: ShortArray, sampleRate: Int = SpeechSegmenter.SAMPLE_RATE): ByteArray {
        require(sampleRate == 16_000 || sampleRate == 24_000) { "transcriber accepts 16 or 24 kHz" }
        val dataBytes = samples.size * 2
        require(HEADER_BYTES + dataBytes <= MAX_BYTES) { "clip larger than 32 MB" }
        val buffer = ByteBuffer.allocate(HEADER_BYTES + dataBytes).order(ByteOrder.LITTLE_ENDIAN)
        buffer.put("RIFF".toByteArray(Charsets.US_ASCII))
        buffer.putInt(36 + dataBytes)
        buffer.put("WAVE".toByteArray(Charsets.US_ASCII))
        buffer.put("fmt ".toByteArray(Charsets.US_ASCII))
        buffer.putInt(16)
        buffer.putShort(1) // PCM
        buffer.putShort(1) // mono
        buffer.putInt(sampleRate)
        buffer.putInt(sampleRate * 2)
        buffer.putShort(2)
        buffer.putShort(16)
        buffer.put("data".toByteArray(Charsets.US_ASCII))
        buffer.putInt(dataBytes)
        for (s in samples) buffer.putShort(s)
        return buffer.array()
    }
}
