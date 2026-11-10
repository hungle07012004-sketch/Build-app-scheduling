import copy
import random
from types import SimpleNamespace
import unittest
from experiments.ordering_sensitivity.run import random_move, release_move, pair_move


class MoveTests(unittest.TestCase):
    def decoder(self):
        return SimpleNamespace(jobs=['A','B','C','D'],
            product=dict(A='X',B='Y',C='Y',D='X'),
            release=dict(A=40,B=30,C=20,D=10),
            element=dict(A=0,B=0,C=0,D=0),loop=dict(A=0,B=0,C=0,D=0),
            qty=dict(A=15,B=10,C=10,D=15),batch_key=dict(A='same',B='other',C='other',D='same'))

    def test_release_swaps_inversion_preserving_product_slots(self):
        dec=self.decoder();ch=(0,1,2,3)
        child=release_move(ch,dec,random.Random(42))
        self.assertEqual(sorted(child),list(ch))
        self.assertEqual([dec.product[dec.jobs[g]] for g in child],['X','Y','Y','X'])
        def inversions(seq):
            return sum(dec.release[dec.jobs[a]]>dec.release[dec.jobs[b]]
                for i,a in enumerate(seq) for b in seq[i+1:]
                if dec.product[dec.jobs[a]]==dec.product[dec.jobs[b]])
        self.assertLess(inversions(child),inversions(ch))

    def test_pair_moves_only_existing_same_key_remainders(self):
        dec=self.decoder();ch=(0,1,2,3)
        for seed in range(20):
            child=pair_move(ch,dec,random.Random(seed))
            self.assertEqual(sorted(child),list(ch))
            self.assertEqual(abs(child.index(0)-child.index(3)),1)

    def test_no_target_returns_none(self):
        dec=self.decoder()
        self.assertIsNone(pair_move((0,3,1,2),dec,random.Random(42)))
        self.assertIsNone(release_move((3,2,1,0),dec,random.Random(42)))

    def test_operators_deterministic_and_do_not_mutate_inputs(self):
        dec=self.decoder();before=copy.deepcopy(vars(dec));ch=(0,1,2,3)
        for move in (release_move,pair_move):
            self.assertEqual(move(ch,dec,random.Random(1051)),move(ch,dec,random.Random(1051)))
        self.assertEqual(vars(dec),before);self.assertEqual(ch,(0,1,2,3))

    def test_random_control_preserves_permutation(self):
        for seed in range(100):
            self.assertEqual(sorted(random_move(tuple(range(166)),random.Random(seed))),list(range(166)))


if __name__=='__main__':unittest.main()
