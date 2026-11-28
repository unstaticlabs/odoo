{
    "name": "USL Voice",
    "summary": "Dictate text and share transcribed voice notes",
    "version": "saas~19.3.1.0.0",
    "category": "Productivity",
    "author": "Unstatic Labs",
    "license": "LGPL-3",
    "depends": ["mail", "web", "portal", "queue_job"],
    "data": ["data/voice_jobs.xml"],
    "assets": {
        "web.assets_backend": ["usl_voice/static/src/**/*"],
        "portal.assets_chatter": ["usl_voice/static/src/transcripts.js", "usl_voice/static/src/chatter.xml"],
        "web.assets_tests": ["usl_voice/static/tests/tours/*.js"],
        "web.assets_unit_tests": ["usl_voice/static/tests/**/*.test.js"],
    },
    "installable": True,
}
