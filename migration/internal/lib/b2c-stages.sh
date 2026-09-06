#!/usr/bin/env bash

# The B2C reconstruction stages, shared by the local runner and the deployed
# runner so a stage cannot mean one thing against a disposable database and
# another against a live one.

USL_B2C_SCRIPT_DIR="migration/b2c_restore/addons/usl_b2c_restore/scripts"
readonly USL_B2C_SCRIPT_DIR

# Print the script a stage runs, or fail for a stage that runs no script.
usl_b2c_stage_script() {
    case "$1" in
        import) printf '%s/run_restore.py' "$USL_B2C_SCRIPT_DIR" ;;
        catalog) printf '%s/materialize_catalog.py' "$USL_B2C_SCRIPT_DIR" ;;
        native-history|native-history-dry-run)
            printf '%s/materialize_native_history.py' "$USL_B2C_SCRIPT_DIR"
            ;;
        reclassify-marketing)
            printf '%s/reclassify_marketing_cost.py' "$USL_B2C_SCRIPT_DIR"
            ;;
        tag-assets) printf '%s/tag_gbc_assets.py' "$USL_B2C_SCRIPT_DIR" ;;
        validate) printf '%s/validate_restore.py' "$USL_B2C_SCRIPT_DIR" ;;
        finalize) printf '%s/finalize_restore.py' "$USL_B2C_SCRIPT_DIR" ;;
        *) return 1 ;;
    esac
}

# Print the native-history mode a stage implies. Empty for every other stage.
usl_b2c_stage_history_mode() {
    case "$1" in
        native-history) printf 'apply' ;;
        native-history-dry-run) printf 'dry_run' ;;
        *) printf '' ;;
    esac
}
