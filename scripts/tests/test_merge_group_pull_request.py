import runpy
import unittest
from pathlib import Path

select = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'merge-group-pull-request'))['select_pull_request']

class MergeGroupPullRequestTests(unittest.TestCase):
    def test_resolves_source_parent_without_commit_association(self):
        commit = {'parents': [{'sha': 'base'}, {'sha': 'source'}]}
        pr = {'number': 105, 'state': 'open', 'base': {'ref': '19-usl'}, 'head': {'sha': 'source'}}
        unrelated = {**pr, 'number': 104, 'base': {'ref': '19-usl-staging'}}
        self.assertEqual(select(commit, [unrelated, pr], '19-usl'), [pr])

    def test_missing_or_ambiguous_source_does_not_qualify(self):
        commit = {'parents': [{'sha': 'base'}, {'sha': 'source'}]}
        pr = {'state': 'open', 'base': {'ref': '19-usl'}, 'head': {'sha': 'source'}}
        for pulls in ([], [pr, pr], [{**pr, 'state': 'closed'}]):
            with self.assertRaises(ValueError):
                select(commit, pulls, '19-usl')
