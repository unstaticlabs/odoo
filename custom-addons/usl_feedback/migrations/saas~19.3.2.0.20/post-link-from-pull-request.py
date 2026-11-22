"""Copy the deprecated feedback pull request URL into the generic task Link."""
import logging

_logger = logging.getLogger(__name__)


def copy_pull_request_urls(cr):
    """Fill an empty github_url from usl_feedback_pr_url; never overwrite a link."""
    cr.execute(
        """
            UPDATE project_task
               SET github_url = usl_feedback_pr_url
             WHERE COALESCE(usl_feedback_pr_url, '') <> ''
               AND COALESCE(github_url, '') = ''
        """,
    )
    copied = cr.rowcount
    cr.execute(
        """
            SELECT count(*) FROM project_task
             WHERE COALESCE(usl_feedback_pr_url, '') <> ''
               AND COALESCE(github_url, '') <> ''
               AND github_url <> usl_feedback_pr_url
        """,
    )
    kept = cr.fetchone()[0]
    _logger.info(
        "usl_feedback: copied %s pull request URL(s) into the task Link; "
        "kept %s existing different Link value(s)", copied, kept,
    )
    return copied, kept


def migrate(cr, version):
    del version
    copy_pull_request_urls(cr)
