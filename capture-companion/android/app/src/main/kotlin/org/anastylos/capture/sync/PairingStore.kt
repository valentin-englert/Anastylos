package org.anastylos.capture.sync

import android.content.Context
import android.content.SharedPreferences
import androidx.core.content.edit
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/**
 * Workstation pairing secrets at rest: Keystore-backed encryption,
 * excluded from every backup transport (see `res/xml/backup_rules.xml`,
 * filename `secret_prefs` is part of that contract).
 *
 * Stored per pairing: endpoint, workstation id, Bearer token, and the
 * operator-verified pin the TLS trust enforces — exactly one of the two
 * pin flavors below. Manual pairings pin the certificate DER bytes;
 * QR pairings pin the SPKI bytes from the scanned payload, so a cert
 * renewal that keeps the key needs no re-pairing.
 */
data class StoredPairing(
    val baseUrl: String,
    val workstationId: String,
    val token: String,
    /** Lowercase hex SHA-256 over the pinned certificate's DER bytes. Blank unless a manual pairing. */
    val pinnedCertSha256Hex: String = "",
    /** Lowercase hex SPKI SHA-256. Blank unless a QR pairing. */
    val pinnedSpkiSha256Hex: String = "",
    /**
     * Guided-control source identity + scoped token from the
     * guided enroll exchange. Blank until the first guided
     * exchange; additive keys, so pre-guided installs load
     * with blanks and enroll on demand.
     */
    val guidedSourceId: String = "",
    val guidedToken: String = "",
    /** ISO-8601 UTC expiry of [guidedToken]. Blank when unknown. */
    val guidedTokenExpiresAt: String = "",
)

interface PairingStore {
    fun load(): StoredPairing?
    fun save(pairing: StoredPairing)
    fun clear()
}

// security-crypto 1.1.0 deprecates this whole surface (MasterKey,
// EncryptedSharedPreferences) in favor of direct Tink use. The pinned
// 1.1.0 behavior is correct on minSdk 26, so Slice A ships it with this
// suppression; the Tink migration is an M2 follow-up, recorded in
// LIMITATIONS.md.
@Suppress("DEPRECATION")
class EncryptedPairingStore(context: Context) : PairingStore {
    private val prefs: SharedPreferences by lazy {
        val masterKey = MasterKey.Builder(context)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()
        EncryptedSharedPreferences.create(
            context,
            PREFS_NAME,
            masterKey,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        )
    }

    override fun load(): StoredPairing? {
        val baseUrl = prefs.getString(KEY_BASE_URL, null)
        val workstationId = prefs.getString(KEY_WORKSTATION_ID, null)
        val token = prefs.getString(KEY_TOKEN, null)
        val pin = prefs.getString(KEY_PIN, null).orEmpty()
        val spki = prefs.getString(KEY_SPKI, null).orEmpty()
        if (baseUrl.isNullOrBlank() || workstationId.isNullOrBlank() ||
            token.isNullOrBlank() || (pin.isBlank() && spki.isBlank())
        ) {
            return null
        }
        return StoredPairing(
            baseUrl, workstationId, token, pin, spki,
            prefs.getString(KEY_GUIDED_SOURCE, null).orEmpty(),
            prefs.getString(KEY_GUIDED_TOKEN, null).orEmpty(),
            prefs.getString(KEY_GUIDED_EXPIRY, null).orEmpty(),
        )
    }

    override fun save(pairing: StoredPairing) {
        prefs.edit {
            putString(KEY_BASE_URL, pairing.baseUrl)
            putString(KEY_WORKSTATION_ID, pairing.workstationId)
            putString(KEY_TOKEN, pairing.token)
            putString(KEY_PIN, pairing.pinnedCertSha256Hex)
            putString(KEY_SPKI, pairing.pinnedSpkiSha256Hex)
            putString(KEY_GUIDED_SOURCE, pairing.guidedSourceId)
            putString(KEY_GUIDED_TOKEN, pairing.guidedToken)
            putString(KEY_GUIDED_EXPIRY, pairing.guidedTokenExpiresAt)
        }
    }

    override fun clear() {
        prefs.edit { clear() }
    }

    companion object {
        /** Backup-excluded by res/xml/backup_rules.xml. Never rename freely. */
        const val PREFS_NAME = "secret_prefs"
        private const val KEY_BASE_URL = "base_url"
        private const val KEY_WORKSTATION_ID = "workstation_id"
        private const val KEY_TOKEN = "token"
        private const val KEY_PIN = "pinned_cert_sha256"
        private const val KEY_SPKI = "pinned_spki_sha256"
        private const val KEY_GUIDED_SOURCE = "guided_source_id"
        private const val KEY_GUIDED_TOKEN = "guided_token"
        private const val KEY_GUIDED_EXPIRY = "guided_token_expires_at"
    }
}

/** In-memory store for tests and previews. Never ships secrets to disk. */
class EphemeralPairingStore : PairingStore {
    private var pairing: StoredPairing? = null

    override fun load(): StoredPairing? = pairing
    override fun save(pairing: StoredPairing) {
        this.pairing = pairing
    }

    override fun clear() {
        pairing = null
    }
}
