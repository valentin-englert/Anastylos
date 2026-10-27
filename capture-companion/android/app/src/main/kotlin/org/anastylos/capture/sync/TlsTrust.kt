package org.anastylos.capture.sync

import java.io.ByteArrayInputStream
import java.net.InetSocketAddress
import java.net.Socket
import java.security.MessageDigest
import java.security.SecureRandom
import java.security.cert.CertificateException
import android.annotation.SuppressLint
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate
import javax.net.ssl.HostnameVerifier
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocket
import javax.net.ssl.SSLSocketFactory
import javax.net.ssl.TrustManager
import javax.net.ssl.X509TrustManager

/**
 * Workstation-specific TLS trust without certificate installation.
 *
 * Trust bootstrap (TOFU with an explicit operator check):
 * 1. [peekServerCertificates] completes a TLS handshake with a manager
 *    that captures the peer chain and then ABORTS — no HTTP request, no
 *    pairing code, no token ever flows through the unverified session.
 * 2. The caller shows the leaf SHA-256 over the raw DER bytes and the
 *    operator compares it against the fingerprint the receiver prints.
 * 3. Only on an exact match does [pinnedSocketFactory] build a session
 *    whose manager ([PinnedTrustManager]) accepts exactly that
 *    certificate — still checking its validity period. The
 *    CustomX509TrustManager suppressions below are reviewed, not blanket:
 *    the managers accept one exact pinned certificate (constant-time
 *    compare) after the platform validity check, and TlsTrustTest proves
 *    accept/reject/empty/expired behavior.
 *
 * What this authenticates: the peer holds the private key of the exact
 * certificate the operator verified. What it does not: bind the
 * workstation id to anything (the id is self-asserted by the server),
 * or survive a certificate change — any new certificate needs a fresh
 * operator comparison (M2 automates the comparison via QR, not the trust).
 *
 * No trust-all manager exists on this path: every handshake either
 * aborts or matches the pinned bytes.
 */
fun certSha256Hex(cert: X509Certificate): String =
    MessageDigest.getInstance("SHA-256").digest(cert.encoded).toHexLower()

fun spkiSha256Hex(cert: X509Certificate): String =
    spkiSha256Bytes(cert).toHexLower()

fun spkiSha256Bytes(cert: X509Certificate): ByteArray {
    val spki = cert.publicKey?.encoded
        ?: throw CertificateException("certificate carries no public key")
    return MessageDigest.getInstance("SHA-256").digest(spki)
}

private fun ByteArray.toHexLower(): String {
    val hex = "0123456789abcdef"
    val out = StringBuilder(size * 2)
    for (b in this) {
        val v = b.toInt() and 0xff
        out.append(hex[v ushr 4]).append(hex[v and 0x0f])
    }
    return out.toString()
}

fun parseHexSha256(text: String): ByteArray {
    val clean = text.trim().lowercase().replace(":", "").replace(" ", "")
    require(clean.length == 64 && clean.all { it in '0'..'9' || it in 'a'..'f' }) {
        "fingerprint must be 64 lowercase hex characters"
    }
    return ByteArray(32) { i -> clean.substring(i * 2, i * 2 + 2).toInt(16).toByte() }
}

/**
 * Captures the peer chain, then always aborts. Nothing is ever trusted.
 *
 * Lint flags every custom trust manager; this one cannot over-trust by
 * construction (it unconditionally throws) and TlsTrustTest pins that
 * behavior: capture happens, the handshake aborts, clients refuse.
 */
@SuppressLint("CustomX509TrustManager")
class PeekTrustManager : X509TrustManager {
    var peerChain: Array<X509Certificate>? = null
        private set

    override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) {
        throw CertificateException("client certificates are not used")
    }

    override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {
        peerChain = chain.copyOf()
        throw CertificateException("fingerprint peek only; connection is aborted")
    }

    override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
}

/**
 * Runs one TLS handshake against [host]:[port], captures the presented
 * chain, and returns it. The handshake is aborted before any HTTP byte
 * can flow, so this reveals nothing to the peer beyond the ClientHello.
 */
fun peekServerCertificates(host: String, port: Int, timeoutMillis: Int = 10_000): Array<X509Certificate> {
    val peek = PeekTrustManager()
    val context = SSLContext.getInstance("TLS")
    context.init(null, arrayOf<TrustManager>(peek), SecureRandom())
    val socket = context.socketFactory.createSocket() as SSLSocket
    try {
        socket.soTimeout = timeoutMillis
        socket.connect(InetSocketAddress(host, port), timeoutMillis)
        try {
            socket.startHandshake()
        } catch (e: Exception) {
            // Expected: the peek manager aborts every handshake (possibly
            // wrapped in an SSLException by the provider). Anything that
            // arrives before the chain was captured is a real failure.
            if (peek.peerChain == null) throw e
        }
    } finally {
        try {
            socket.close()
        } catch (_: Exception) {
        }
    }
    return peek.peerChain ?: throw CertificateException("server presented no certificate chain")
}

/**
 * Accepts exactly the pinned leaf certificate (constant-time compare),
 * after the platform checks its validity period. Hostname verification
 * is subsumed by the pin: whoever presents these exact bytes and
 * completes the handshake holds the pinned private key, which binds
 * identity more strongly than a name match.
 */
@SuppressLint("CustomX509TrustManager")
class PinnedTrustManager(expectedCertSha256: ByteArray) : X509TrustManager {
    private val expected = expectedCertSha256.copyOf()

    override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) {
        throw CertificateException("client certificates are not used")
    }

    override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {
        if (chain.isEmpty()) throw CertificateException("server presented no certificate")
        chain[0].checkValidity()
        val actual = MessageDigest.getInstance("SHA-256").digest(chain[0].encoded)
        if (!MessageDigest.isEqual(actual, expected)) {
            throw CertificateException("workstation identity mismatch: pinned certificate differs")
        }
    }

    override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
}

data class PinnedTls(
    val socketFactory: SSLSocketFactory,
    /**
     * Permissive by design AND safe by construction: it is only ever
     * installed together with [PinnedTrustManager] or
     * [SpkiPinnedTrustManager], where the pin has already established
     * peer identity at handshake time.
     */
    val hostnameVerifier: HostnameVerifier,
)

fun pinnedSocketFactory(pinnedCertSha256: ByteArray): PinnedTls {
    val context = SSLContext.getInstance("TLS")
    context.init(null, arrayOf<TrustManager>(PinnedTrustManager(pinnedCertSha256)), SecureRandom())
    return PinnedTls(context.socketFactory, HostnameVerifier { _, _ -> true })
}

/**
 * Thrown when the peer's public key differs from the scanned SPKI pin.
 * A dedicated type (not a message) so callers can tell a key mismatch
 * — never silently retryable — apart from transport failures.
 */
class SpkiMismatchException : CertificateException("peer public key differs from the pinned SPKI")

/**
 * Accepts the peer whose *leaf* public key hashes to the pinned SPKI
 * (constant-time compare), after the platform checks the leaf's
 * validity period. Leaf-only is the whole contract: the receiver
 * presents a single self-signed certificate, so chain-wide pinning
 * would add no security, only ambiguity. A renewal that keeps the
 * key reconnects without re-pairing; a key change fails closed and
 * needs a fresh QR.
 *
 * Hostname verification is subsumed by the pin, exactly as with
 * [PinnedTrustManager]: whoever completes the handshake with the
 * pinned key holds the private key the QR vouched for.
 */
@SuppressLint("CustomX509TrustManager")
class SpkiPinnedTrustManager(expectedSpkiSha256: ByteArray) : X509TrustManager {
    private val expected = expectedSpkiSha256.copyOf()

    override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) {
        throw CertificateException("client certificates are not used")
    }

    override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {
        if (chain.isEmpty()) throw CertificateException("server presented no certificate")
        chain[0].checkValidity()
        val actual = spkiSha256Bytes(chain[0])
        if (!MessageDigest.isEqual(actual, expected)) {
            throw SpkiMismatchException()
        }
    }

    override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
}

fun pinnedSpkiSocketFactory(pinnedSpkiSha256: ByteArray): PinnedTls {
    val context = SSLContext.getInstance("TLS")
    context.init(null, arrayOf<TrustManager>(SpkiPinnedTrustManager(pinnedSpkiSha256)), SecureRandom())
    return PinnedTls(context.socketFactory, HostnameVerifier { _, _ -> true })
}

fun loadPemCertificates(pemBytes: ByteArray): List<X509Certificate> {
    val factory = CertificateFactory.getInstance("X.509")
    return factory.generateCertificates(ByteArrayInputStream(pemBytes))
        .filterIsInstance<X509Certificate>()
}

/** Plain TCP reachability without TLS; used for honest offline detection. */
fun canReach(host: String, port: Int, timeoutMillis: Int = 3_000): Boolean {
    return try {
        Socket().use { socket ->
            socket.connect(InetSocketAddress(host, port), timeoutMillis)
            true
        }
    } catch (_: Exception) {
        false
    }
}
