"""The structure lookup's rules, offline: which ID a text names, and which PDB
entry is the biologic itself rather than a relative, a partner or a mutant.

    python test_structures.py
"""

import structures as S


def entry(eid, title, chains, residues, resolution=2.0, mutations=0, human=True, copies=1):
    return {
        "rcsb_id": eid,
        "struct": {"title": title},
        "exptl": [{"method": "X-RAY DIFFRACTION"}],
        "rcsb_entry_info": {"deposited_polymer_monomer_count": residues * copies,
                            "resolution_combined": [resolution]},
        "polymer_entities": [{
            "rcsb_polymer_entity": {"pdbx_description": d},
            "rcsb_polymer_entity_container_identifiers": {"asym_ids": ["A"] * copies},
            "entity_poly": {"rcsb_entity_polymer_type": "Protein", "rcsb_mutation_count": mutations,
                            "rcsb_sample_sequence_length": residues // len(chains)},
            "rcsb_entity_source_organism": [{"scientific_name": "Homo sapiens" if human else "Mus musculus"}],
        } for d in chains],
    }


def main():
    # An ID written in the text is used as is; numbers with units are not IDs.
    assert S.explicit_id("simulate against 1igt please") == "1IGT"
    assert S.explicit_id("P01857") == "P01857"
    assert S.explicit_id("stored at 25C for 24 months") is None
    assert S.explicit_id("adalimumab") is None
    print("ok  an ID in the text wins; temperatures and durations are not IDs")

    # The name is found inside a sentence; generic words are not searched for.
    assert S.search_terms("Humira (adalimumab), a TNF antibody")[:2] == ["adalimumab", "Humira"]
    assert S.search_terms("my IgG1 antibody") == []
    assert S.naming_phrase("human insulin, stored at 25 C") == "human insulin"
    ask = "find stabilizers for human insulin that could maintain its stability in 35 degree celcius"
    assert S.search_terms(ask) == ["insulin"], S.search_terms(ask)
    assert S.naming_phrase(ask) == "human insulin"
    print("ok  the biologic's name is picked out of a sentence")

    # A description is the biologic itself, not a relative or a partner.
    yes = ["Adalimumab Heavy Chain", "Trastuzumab anti-HER2 Fab Light Chain", "Insulin A chain", "Ubiquitin"]
    no = ["Insulin receptor", "Insulin-like growth factor I", "Ubiquitin-conjugating enzyme E2 D2",
          "Tumor necrosis factor", "Heavy chain of adalimumab EFab (VH-IgE CH2)"]
    assert all(S.is_the_biologic(d, d.split()[0].split("-")[0]) for d in yes[:1]), yes
    assert S.is_the_biologic(yes[1], "trastuzumab") and S.is_the_biologic(yes[2], "insulin")
    assert S.is_the_biologic(yes[3], "ubiquitin")
    assert not any(S.is_the_biologic(d, n) for d, n in zip(
        no, ["insulin", "insulin", "ubiquitin", "adalimumab", "adalimumab"]))
    print("ok  a receptor, an enzyme, a binding partner or an engineered hybrid is not the biologic")

    entries = [
        entry("3WD5", "TNFalpha in complex with Adalimumab Fab",
              ["Adalimumab Heavy Chain", "Adalimumab Light Chain", "Tumor necrosis factor"], 589),
        entry("6CR1", "adalimumab EFab", ["Heavy chain of adalimumab EFab (VH-IgE CH2)",
                                          "Light chain of adalimumab EFab (VL-IgE CH2)"], 442),
        entry("9MUT", "Adalimumab Fab D185A mutant", ["Adalimumab Heavy Chain", "Adalimumab Light Chain"], 440,
              resolution=1.2),
        entry("4NYL", "Crystal structure of adalimumab FAB fragment",
              ["Adalimumab Heavy Chain", "Adalimumab Light Chain"], 444, copies=4),
        entry("9BIG", "Adalimumab Fab", ["Adalimumab Heavy Chain", "Adalimumab Light Chain"], 800, copies=2),
    ]
    picks = S._rank(entries, "adalimumab")
    ids = [p["structure_id"] for p in picks]
    assert ids[0] == "4NYL", ids
    assert "3WD5" not in ids and "6CR1" not in ids, "a complex or a hybrid was offered"
    assert ids.index("9MUT") > ids.index("4NYL"), "a mutant outranked the unmodified molecule"
    assert picks[0]["residues"] == 444 and "4 copies" in picks[0]["why"]
    assert "9BIG" not in ids or picks[ids.index("9BIG")]["residues"] <= S.MAX_RESIDUES
    print("ok  adalimumab: the plain Fab, one copy of four, over its mutant, its complex with TNF and a hybrid")

    # Antibody chains labelled only "heavy chain" count when the title names the drug.
    whole = [entry("5DK3", "Crystal structure of pembrolizumab, a full length IgG4 antibody",
                   ["Heavy chain", "Light chain"], 1300)]
    assert [p["structure_id"] for p in S._rank(whole, "pembrolizumab")] == ["5DK3"]
    assert S._rank(whole, "nivolumab") == []
    print("ok  generically labelled chains count only under a title that names the biologic")

    # A plain title that says what the user wrote beats one that merely mentions it.
    ins = [entry("1UZ9", "Studies of N-lithocholyl insulin", ["Insulin A chain", "Insulin B chain"], 50),
           entry("6O17", "Recombinant Human Insulin", ["Insulin A chain", "Insulin B chain"], 51)]
    assert S._rank(ins, "insulin", "human insulin")[0]["structure_id"] == "6O17"
    print("ok  human insulin: the entry titled as such first")

    # A misspelt name is read as the biologic it is a near-miss for. Neither the
    # PDB nor UniProt tolerates a typo, so without this one wrong letter ends the
    # simulation with "no structure found".
    assert S.corrected_terms("adamalimumab") == [("adalimumab", "adamalimumab")]
    assert S.corrected_terms("stabilize adamalimumab at 40 degree celcius") == \
        [("adalimumab", "adamalimumab")]
    assert S.corrected_terms("insulan") == [("insulin", "insulan")]
    assert S.corrected_terms("lysozime") == [("lysozyme", "lysozime")]
    # A name spelt correctly is left alone, and so is a word that is not a drug.
    assert S.corrected_terms("adalimumab") == []
    assert S.corrected_terms("my IgG1 antibody") == []
    assert S.corrected_terms("somethingelse entirely") == []
    print("ok  a misspelt biologic is read as the name it is a near-miss for")

    # A brand name is the same molecule under the name on the vial, and the
    # databases only know the INN. The chain matters: Neupogen is filgrastim,
    # and filgrastim is what the PDB calls granulocyte colony-stimulating factor.
    assert S.OTHER_NAMES["humira"] == "adalimumab"
    assert [t for t, _ in S.expand_terms(["Humira"])] == ["Humira", "adalimumab"]
    assert [t for t, _ in S.expand_terms(["Neupogen"])] == \
        ["Neupogen", "filgrastim", "granulocyte colony-stimulating factor"]
    # What the user wrote is still tried first, in case the brand is in the PDB.
    assert S.expand_terms(["Herceptin"])[0][0] == "Herceptin"
    # A name that is nobody's synonym passes through untouched.
    assert [t for t, _ in S.expand_terms(["lysozyme"])] == ["lysozyme"]
    print("ok  a brand name is followed to the molecule the databases know")

    # A UniProt hit has to BE the biologic, not merely contain the word. A yeast
    # protein is called "Needs CLA4 to survive protein 3", and "survive" is a
    # word people write about formulations -- that match once sent a request for
    # an antibody to a sulfurtransferase from baker's yeast.
    yeast = ["Adenylyltransferase and sulfurtransferase UBA4",
             "Needs CLA4 to survive protein 3"]
    assert S._names_it(yeast, "survive") is False
    assert S._names_it(["Insulin", "Insulin A chain"], "insulin") is True
    assert S._names_it(["Lysozyme C"], "lysozyme") is True
    print("ok  a protein whose name merely contains the word is not the biologic")

    # The one thing worse than not correcting: correcting to a DIFFERENT
    # molecule. Antibody names differ by a syllable, so every single-slip typo
    # of every name must land on its own molecule or on nothing at all.
    def slips(name):
        out = set()
        for i in range(1, len(name) - 1):
            out.add(name[:i] + name[i + 1:])
            out.add(name[:i] + name[i] + name[i:])
            out.add(name[:i] + name[i + 1] + name[i] + name[i + 2:])
        return {t for t in out if t and t != name}

    def same_molecule(a, b):
        return (a == b or S.OTHER_NAMES.get(a) == b or S.OTHER_NAMES.get(b) == a
                or {a, b} == {"hemoglobin", "haemoglobin"})

    for name in S.TYPO_VOCABULARY:
        for typo in slips(name):
            got = S.corrected_terms(typo)
            if got:
                assert same_molecule(got[0][0], name), f"{name} -> {typo} -> {got[0][0]}"
    print(f"ok  no typo of any of the {len(S.TYPO_VOCABULARY)} names reads as a different molecule")

    # ...and no word a formulator writes is mistaken for a drug. "albumen" is
    # allowed to read as albumin: egg white and the protein, one intent.
    for word in ("protect", "protects", "protein", "solution", "stable", "storage",
                 "shipping", "buffer", "saline", "syringe", "potency", "insulate",
                 "collagen", "gelatin", "heparin", "dextran", "chitosan", "mannitol"):
        assert S.corrected_terms(word) == [], f"{word} -> {S.corrected_terms(word)}"
    print("ok  formulation words are not mistaken for a drug")
    print("\nall checks passed")


if __name__ == "__main__":
    main()
