"""Fixed asset, depreciation and deferral schedule row sources."""

from odoo import models


class RebuildAccountReportExportWizard(models.TransientModel):
    _inherit = "rebuild.account.report.export.wizard"

    def _fixed_asset_rows(self):
        filter_sql, filter_params = self._asset_account_filter_sql()
        self.env.cr.execute(
            f"""
            WITH asset_values AS (
                SELECT asset.id,
                       COALESCE(
                           sum(schedule.amount) FILTER (
                               WHERE schedule.type = 'depreciate'
                                 AND schedule.line_date <= %s
                                 AND (
                                     schedule.init_entry
                                     OR depreciation_move.state = 'posted'
                                 )
                           ),
                           0
                       ) AS accumulated_depreciation
                  FROM account_asset asset
                  LEFT JOIN account_asset_line schedule
                    ON schedule.asset_id = asset.id
                  LEFT JOIN account_move depreciation_move
                    ON depreciation_move.id = schedule.move_id
                 GROUP BY asset.id
            )
            SELECT asset.id::text AS source_asset_id,
                   asset.name,
                   asset.name AS asset_name,
                   COALESCE(asset.date_start::text, '') AS acquisition_date,
                   CASE asset.state
                       WHEN 'draft' THEN 'Brouillon'
                       WHEN 'open' THEN 'En cours'
                       WHEN 'paused' THEN 'En pause'
                       WHEN 'close' THEN 'Clôturée'
                       WHEN 'cancelled' THEN 'Annulée'
                       ELSE asset.state
                   END AS state,
                   ''::text AS asset_group_name,
                   round(asset.purchase_value::numeric, 2)::text AS original_value,
                   round(asset_values.accumulated_depreciation::numeric, 2)::text AS accumulated_depreciation,
                   round(asset_values.accumulated_depreciation::numeric, 2)::text AS depreciation_amount,
                   round((
                       asset.purchase_value
                       - asset_values.accumulated_depreciation
                   )::numeric, 2)::text AS imported_period_net_value,
                   round(asset.value_residual::numeric, 2)::text AS source_book_value,
                   COALESCE(asset_account.code_store->>company.id::text, asset_account.code_store->>'1', asset_account.code_store::text, '') AS asset_account,
                   COALESCE(asset_account.code_store->>company.id::text, asset_account.code_store->>'1', asset_account.code_store::text, '') AS account_code,
                   COALESCE(asset_account.name->>'fr_FR', asset_account.name->>'en_US', asset_account.name::text, '') AS account_name,
                   COALESCE(depreciation_account.code_store->>company.id::text, depreciation_account.code_store->>'1', depreciation_account.code_store::text, '') AS depreciation_account,
                   COALESCE(expense_account.code_store->>company.id::text, expense_account.code_store->>'1', expense_account.code_store::text, '') AS depreciation_expense_account
              FROM account_asset asset
              JOIN res_company company ON company.id = asset.company_id
              JOIN account_asset_profile profile ON profile.id = asset.profile_id
              JOIN asset_values ON asset_values.id = asset.id
              LEFT JOIN account_account asset_account ON asset_account.id = profile.account_asset_id
              LEFT JOIN account_account depreciation_account ON depreciation_account.id = profile.account_depreciation_id
              LEFT JOIN account_account expense_account ON expense_account.id = profile.account_expense_depreciation_id
             WHERE asset.company_id = %s
               AND asset.date_start <= %s
               {filter_sql}
             ORDER BY account_code, asset.id
            """,
            [
                self.date_to,
                self.company_id.id,
                self.date_to,
                *filter_params,
            ],
        )
        return [dict(row) for row in self.env.cr.dictfetchall()]

    def _fixed_asset_group_account_rows(self):
        filter_sql, filter_params = self._asset_account_filter_sql()
        self.env.cr.execute(
            f"""
            WITH asset_values AS (
                SELECT asset.id,
                       COALESCE(
                           sum(schedule.amount) FILTER (
                               WHERE schedule.type = 'depreciate'
                                 AND schedule.line_date <= %s
                                 AND (
                                     schedule.init_entry
                                     OR depreciation_move.state = 'posted'
                                 )
                           ),
                           0
                       ) AS accumulated_depreciation
                  FROM account_asset asset
                  LEFT JOIN account_asset_line schedule
                    ON schedule.asset_id = asset.id
                  LEFT JOIN account_move depreciation_move
                    ON depreciation_move.id = schedule.move_id
                 GROUP BY asset.id
            )
            SELECT asset_account.id::text AS source_account_id,
                   COALESCE(asset_account.code_store->>company.id::text, asset_account.code_store->>'1', asset_account.code_store::text, '') AS account_code,
                   COALESCE(asset_account.name->>'fr_FR', asset_account.name->>'en_US', asset_account.name::text, '') AS account_name,
                   count(asset.id)::text AS asset_count,
                   string_agg(asset.name, '; ' ORDER BY asset.id) AS asset_names,
                   round(sum(asset.purchase_value)::numeric, 2)::text AS original_value,
                   round(sum(asset_values.accumulated_depreciation)::numeric, 2)::text AS accumulated_depreciation,
                   round(sum(asset_values.accumulated_depreciation)::numeric, 2)::text AS depreciation_amount,
                   round(sum(
                       asset.purchase_value
                       - asset_values.accumulated_depreciation
                   )::numeric, 2)::text AS imported_period_net_value,
                   round(sum(asset.value_residual)::numeric, 2)::text AS source_book_value
              FROM account_asset asset
              JOIN res_company company ON company.id = asset.company_id
              JOIN account_asset_profile profile ON profile.id = asset.profile_id
              JOIN asset_values ON asset_values.id = asset.id
              LEFT JOIN account_account asset_account ON asset_account.id = profile.account_asset_id
             WHERE asset.company_id = %s
               AND asset.date_start <= %s
               {filter_sql}
             GROUP BY asset_account.id,
                      asset_account.id,
                      COALESCE(asset_account.code_store->>company.id::text, asset_account.code_store->>'1', asset_account.code_store::text, ''),
                      COALESCE(asset_account.name->>'fr_FR', asset_account.name->>'en_US', asset_account.name::text, '')
             ORDER BY account_code
            """,
            [
                self.date_to,
                self.company_id.id,
                self.date_to,
                *filter_params,
            ],
        )
        return [dict(row) for row in self.env.cr.dictfetchall()]

    def _depreciation_schedule_rows(self):
        filter_sql, filter_params = self._asset_account_filter_sql()
        self.env.cr.execute(
            f"""
            SELECT asset.id::text AS source_asset_id,
                   asset.name AS asset_name,
                   schedule.line_date::text AS depreciation_date,
                   schedule.id::text AS source_move_id,
                   COALESCE(imported_move.name::text, '') AS source_move_name,
                   COALESCE(imported_move.state::text, '') AS source_move_state,
                   CASE
                       WHEN imported_move.state = 'posted' THEN 'Comptabilisée'
                       WHEN imported_move.id IS NOT NULL THEN 'Écriture brouillon'
                       ELSE 'Planifiée'
                   END AS representation_status,
                   CASE
                       WHEN imported_move.state = 'posted' THEN 'Comptabilisée'
                       WHEN imported_move.id IS NOT NULL THEN 'Écriture brouillon'
                       ELSE 'Planifiée'
                   END AS status,
                   COALESCE(imported_move.ref::text, '') AS move_ref,
                   round(schedule.amount::numeric, 2)::text AS expense_amount,
                   round(schedule.amount::numeric, 2)::text AS depreciation_amount,
                   round((schedule.depreciated_value + schedule.amount)::numeric, 2)::text AS accumulated_depreciation_amount,
                   round((schedule.depreciated_value + schedule.amount)::numeric, 2)::text AS accumulated_depreciation,
                   round(schedule.remaining_value::numeric, 2)::text AS net_book_value_after_line,
                   round(schedule.remaining_value::numeric, 2)::text AS imported_period_net_value,
                   COALESCE(imported_move.name::text, '') AS imported_move_name,
                   COALESCE(imported_move.id::text, '') AS imported_source_move_id
              FROM account_asset_line schedule
              JOIN account_asset asset ON asset.id = schedule.asset_id
              JOIN account_asset_profile profile ON profile.id = asset.profile_id
              LEFT JOIN account_move imported_move ON imported_move.id = schedule.move_id
             WHERE asset.company_id = %s
               AND schedule.line_date BETWEEN %s AND %s
               {filter_sql}
             ORDER BY asset.id, schedule.line_date, schedule.id
            """,
            [self.company_id.id, self.date_from, self.date_to, *filter_params],
        )
        rows = [dict(row) for row in self.env.cr.dictfetchall()]
        for row in rows:
            row["section"] = row.get("asset_name") or "Immobilisation"
        return rows

    def _deferred_schedule_rows(self):
        filter_sql, filter_params = self._deferred_schedule_filter_sql()
        self.env.cr.execute(
            f"""
            SELECT deferral.original_move_id::text AS source_original_move_id,
                   COALESCE(schedule.move_id, 0)::text AS source_deferred_move_id,
                   COALESCE(original_move.name::text, '') AS source_original_name,
                   COALESCE(deferred_move.name::text, '') AS source_deferred_name,
                   original_move.state AS source_original_state,
                   COALESCE(deferred_move.state, 'draft') AS source_deferred_state,
                   original_move.move_type AS source_original_move_type,
                   COALESCE(deferred_move.move_type, 'entry') AS source_deferred_move_type,
                   original_move.date::text AS original_date,
                   schedule.date::text AS deferred_date,
                   deferral.start_date::text AS deferred_start_date,
                   deferral.end_date::text AS deferred_end_date,
                   deferral.schedule_type,
                   schedule.phase AS schedule_phase,
                   CASE WHEN schedule.state = 'posted' THEN 'posted' ELSE 'scheduled' END AS representation_status,
                   CASE WHEN schedule.state = 'posted' THEN 'represented' ELSE 'review_required' END AS review_status,
                   COALESCE(deferral_account.code_store->>company.id::text, deferral_account.code_store->>'1', deferral_account.code_store::text, '') AS deferred_account_code,
                   COALESCE(deferral_account.name->>'fr_FR', deferral_account.name->>'en_US', deferral_account.name::text, '') AS deferred_account_name,
                   COALESCE(recognition_account.code_store->>company.id::text, recognition_account.code_store->>'1', recognition_account.code_store::text, '') AS counterpart_account_codes,
                   COALESCE(recognition_account.name->>'fr_FR', recognition_account.name->>'en_US', recognition_account.name::text, '') AS counterpart_account_names,
                   round(abs(schedule.recognition_balance)::numeric, 2)::text AS amount,
                   round(schedule.deferral_balance::numeric, 2)::text AS deferred_account_balance,
                   round(schedule.recognition_balance::numeric, 2)::text AS counterpart_balance,
                   COALESCE(original_move.name::text, '') AS imported_original_move_name,
                   COALESCE(deferred_move.name::text, '') AS imported_deferred_move_name
              FROM rebuild_account_deferral_line schedule
              JOIN rebuild_account_deferral deferral ON deferral.id = schedule.deferral_id
              JOIN res_company company ON company.id = schedule.company_id
              JOIN account_move original_move ON original_move.id = deferral.original_move_id
         LEFT JOIN account_move deferred_move ON deferred_move.id = schedule.move_id
              JOIN account_account deferral_account ON deferral_account.id = deferral.deferral_account_id
              JOIN account_account recognition_account ON recognition_account.id = schedule.recognition_account_id
             WHERE schedule.company_id = %s
               AND schedule.date BETWEEN %s AND %s
               {filter_sql}
             ORDER BY schedule.date, deferral.original_move_id, schedule.id
            """,
            [self.company_id.id, self.date_from, self.date_to, *filter_params],
        )
        rows = [dict(row) for row in self.env.cr.dictfetchall()]
        for row in rows:
            row["section"] = (
                "Produits constatés d’avance"
                if row.get("schedule_type") in {"revenue", "income"}
                else "Charges constatées d’avance"
            )
        return rows
