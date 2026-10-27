package org.anastylos.capture.sync

import com.google.common.truth.Truth.assertThat
import java.math.BigInteger
import java.security.Principal
import java.security.PublicKey
import java.security.cert.CertificateException
import java.security.cert.CertificateExpiredException
import java.security.cert.X509Certificate
import java.util.Date
import org.junit.Test

/**
 * Trust-primitive tests over two checked-in self-signed certificates
 * (`src/test/resources/tls/`; public parts only, keys destroyed at
 * minting). Certificate hashes were computed with `openssl x509
 * -fingerprint -sha256` and the SPKI hash with an independent Python
 * PEM-decode computation at authoring time, so every assertion
 * cross-checks two independent implementations.
 */
class TlsTrustTest {
    private fun cert(name: String): X509Certificate {
        val bytes = requireNotNull(javaClass.getResource("/tls/$name")).readBytes()
        return loadPemCertificates(bytes).single()
    }

    @Test
    fun `certificate fingerprint matches openssl`() {
        assertThat(certSha256Hex(cert("workstation.pem"))).isEqualTo(
            "73556d40a654297a953619b3a08259ce2dfe749d61e6b88e2699614bc715817d",
        )
        assertThat(certSha256Hex(cert("other.pem"))).isEqualTo(
            "214aec98a4c104e18127031477e35b4a00f709251043246ffd34775e49be4c78",
        )
    }

    @Test
    fun `spki fingerprint matches openssl`() {
        // Over the 294-byte SubjectPublicKeyInfo DER (the PEM "PUBLIC KEY"
        // body), cross-checked with an independent Python computation.
        assertThat(spkiSha256Hex(cert("workstation.pem"))).isEqualTo(
            "5b990e9c6186bf1163ff0c7d2cef2f156e92c57a3b4f3cdd43b98800977b9206",
        )
    }

    @Test
    fun `fingerprint parser accepts operator spellings`() {
        val expected = "73556d40a654297a953619b3a08259ce2dfe749d61e6b88e2699614bc715817d"
        val colonUpper = "73:55:6D:40:A6:54:29:7A:95:36:19:B3:A0:82:59:CE:" +
            "2D:FE:74:9D:61:E6:B8:8E:26:99:61:4B:C7:15:81:7D"
        assertThat(parseHexSha256(colonUpper).toHexTest()).isEqualTo(expected)
        assertThat(parseHexSha256("  $expected  ").toHexTest()).isEqualTo(expected)
        for (bad in listOf("", "zz", expected.dropLast(2), expected + "00", "00".repeat(31) + "zz")) {
            try {
                parseHexSha256(bad)
                throw AssertionError("expected rejection of $bad")
            } catch (_: IllegalArgumentException) {
                // Expected.
            }
        }
    }

    @Test
    fun `peek manager captures the chain and always aborts`() {
        val peek = PeekTrustManager()
        val chain = arrayOf(cert("workstation.pem"))
        try {
            peek.checkServerTrusted(chain, "RSA")
            throw AssertionError("expected the peek to abort")
        } catch (e: CertificateException) {
            assertThat(e.message).contains("aborted")
        }
        assertThat(peek.peerChain).isNotNull()
        assertThat(peek.peerChain!!.map { certSha256Hex(it) })
            .containsExactly(certSha256Hex(chain[0]))
        try {
            peek.checkClientTrusted(chain, "RSA")
            throw AssertionError("expected client-auth refusal")
        } catch (_: CertificateException) {
            // Expected.
        }
    }

    @Test
    fun `pinned manager accepts the pinned certificate only`() {
        val pinned = parseHexSha256(certSha256Hex(cert("workstation.pem")))
        val manager = PinnedTrustManager(pinned)
        manager.checkServerTrusted(arrayOf(cert("workstation.pem")), "RSA")

        try {
            manager.checkServerTrusted(arrayOf(cert("other.pem")), "RSA")
            throw AssertionError("expected identity mismatch")
        } catch (e: CertificateException) {
            assertThat(e.message).contains("mismatch")
        }
        try {
            manager.checkServerTrusted(emptyArray(), "RSA")
            throw AssertionError("expected empty-chain refusal")
        } catch (_: CertificateException) {
            // Expected.
        }
    }

    @Test
    fun `pinned manager refuses expired certificates`() {
        val pinned = parseHexSha256(certSha256Hex(cert("workstation.pem")))
        val manager = PinnedTrustManager(pinned)
        // Same bytes as the pinned cert, but already expired: validity is
        // checked before the pin comparison, so this must fail closed.
        val expired = object : DelegatingCertificate(cert("workstation.pem")) {
            override fun checkValidity() {
                throw CertificateExpiredException("test double: expired")
            }

            override fun checkValidity(date: Date) {
                throw CertificateExpiredException("test double: expired")
            }
        }
        try {
            manager.checkServerTrusted(arrayOf(expired), "RSA")
            throw AssertionError("expected expiry refusal")
        } catch (_: CertificateExpiredException) {
            // Expected: the platform validity check runs before the pin.
        }
    }

    @Test
    fun `http transport guards are explicit`() {
        try {
            HttpsTransport("http://192.168.1.2:8443", allowLoopbackHttp = true)
            throw AssertionError("expected non-loopback http refusal")
        } catch (e: IllegalArgumentException) {
            assertThat(e.message).contains("loopback")
        }
        try {
            HttpsTransport("http://127.0.0.1:8443")
            throw AssertionError("expected opt-in requirement")
        } catch (e: IllegalArgumentException) {
            assertThat(e.message).contains("opt-in")
        }
        // These must construct without throwing (no connections are made).
        HttpsTransport("http://127.0.0.1:8443", allowLoopbackHttp = true)
        HttpsTransport("https://192.168.1.2:8443")
    }

    @Test
    fun `spki manager accepts the pinned key only`() {
        val workstationPin = parseHexSha256("5b990e9c6186bf1163ff0c7d2cef2f156e92c57a3b4f3cdd43b98800977b9206")
        val manager = SpkiPinnedTrustManager(workstationPin)
        // OpenSSL-vector pin: the checked-in cert passes against it.
        manager.checkServerTrusted(arrayOf(cert("workstation.pem")), "RSA")
        try {
            manager.checkServerTrusted(arrayOf(cert("other.pem")), "RSA")
            throw AssertionError("expected SPKI refusal for the other key")
        } catch (e: SpkiMismatchException) {
            assertThat(e.message).contains("pinned SPKI")
        }
        try {
            manager.checkServerTrusted(emptyArray(), "RSA")
            throw AssertionError("expected empty-chain refusal")
        } catch (_: CertificateException) {
            // Expected.
        }
        try {
            manager.checkClientTrusted(arrayOf(cert("workstation.pem")), "RSA")
            throw AssertionError("expected client-cert refusal")
        } catch (_: CertificateException) {
            // Expected.
        }
        assertThat(manager.acceptedIssuers).isEmpty()
    }

    @Test
    fun `spki manager refuses expired certificates`() {
        val workstationPin = parseHexSha256("5b990e9c6186bf1163ff0c7d2cef2f156e92c57a3b4f3cdd43b98800977b9206")
        val manager = SpkiPinnedTrustManager(workstationPin)
        val expired = object : DelegatingCertificate(cert("workstation.pem")) {
            override fun checkValidity() {
                throw CertificateExpiredException("test double: expired")
            }

            override fun checkValidity(date: Date) {
                throw CertificateExpiredException("test double: expired")
            }
        }
        try {
            manager.checkServerTrusted(arrayOf(expired), "RSA")
            throw AssertionError("expected expiry refusal")
        } catch (_: CertificateExpiredException) {
            // Expected: validity runs before the pin comparison.
        }
    }

    @Test
    fun `spki socket factory builds a working context`() {
        // No connection is made; this proves the factory wires the
        // manager into a usable SSLContext without throwing.
        val workstationPin = parseHexSha256("5b990e9c6186bf1163ff0c7d2cef2f156e92c57a3b4f3cdd43b98800977b9206")
        val tls = pinnedSpkiSocketFactory(workstationPin)
        assertThat(tls.socketFactory).isNotNull()
        assertThat(tls.socketFactory.supportedCipherSuites).isNotEmpty()
    }

    private fun ByteArray.toHexTest(): String {
        val hex = "0123456789abcdef"
        val out = StringBuilder(size * 2)
        for (b in this) {
            val v = b.toInt() and 0xff
            out.append(hex[v ushr 4]).append(hex[v and 0x0f])
        }
        return out.toString()
    }
}

/** Full-delegation X509 stand-in so single methods can be overridden. */
private open class DelegatingCertificate(private val inner: X509Certificate) : X509Certificate() {
    override fun checkValidity() = inner.checkValidity()
    override fun checkValidity(date: Date) = inner.checkValidity(date)
    override fun getVersion(): Int = inner.version
    override fun getSerialNumber(): BigInteger = inner.serialNumber
    override fun getIssuerDN(): Principal = inner.issuerDN
    override fun getSubjectDN(): Principal = inner.subjectDN
    override fun getNotBefore(): Date = inner.notBefore
    override fun getNotAfter(): Date = inner.notAfter
    override fun getTBSCertificate(): ByteArray = inner.getTBSCertificate()
    override fun getSignature(): ByteArray = inner.signature
    override fun getSigAlgName(): String = inner.sigAlgName
    override fun getSigAlgOID(): String = inner.sigAlgOID
    override fun getSigAlgParams(): ByteArray? = inner.sigAlgParams
    override fun getIssuerUniqueID(): BooleanArray? = inner.issuerUniqueID
    override fun getSubjectUniqueID(): BooleanArray? = inner.subjectUniqueID
    override fun getKeyUsage(): BooleanArray? = inner.keyUsage
    override fun getBasicConstraints(): Int = inner.basicConstraints
    override fun getEncoded(): ByteArray = inner.encoded
    override fun verify(key: PublicKey) = inner.verify(key)
    override fun verify(key: PublicKey, sigProvider: String) = inner.verify(key, sigProvider)
    override fun toString(): String = inner.toString()
    override fun getPublicKey(): PublicKey = inner.publicKey
    override fun hasUnsupportedCriticalExtension(): Boolean = inner.hasUnsupportedCriticalExtension()
    override fun getCriticalExtensionOIDs(): Set<String>? = inner.criticalExtensionOIDs
    override fun getNonCriticalExtensionOIDs(): Set<String>? = inner.nonCriticalExtensionOIDs
    override fun getExtensionValue(oid: String): ByteArray? = inner.getExtensionValue(oid)
}
