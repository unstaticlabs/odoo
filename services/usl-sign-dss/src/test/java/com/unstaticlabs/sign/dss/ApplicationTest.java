package com.unstaticlabs.sign.dss;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.Test;

final class ApplicationTest {
    @Test
    void certificateIdentityMatchesAllSignerTokensRegardlessOfOrderOrAccents() {
        assertTrue(Application.identityMatches(
                "José Martin",
                "CN=MARTIN, JOSE, SERIALNUMBER=FR-123"));
    }

    @Test
    void certificateIdentityCannotMatchANameAsASubstring() {
        assertFalse(Application.identityMatches("Joan Martin", "CN=Joanne Martin"));
    }

    @Test
    void certificateIdentityFailsClosedForMissingOrPartialNames() {
        assertFalse(Application.identityMatches("", "CN=Alice Dupont"));
        assertFalse(Application.identityMatches("Alice Dupont", "CN=Alice Durant"));
        assertFalse(Application.identityMatches("Alice Dupont", ""));
    }
}
