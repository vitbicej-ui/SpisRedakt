import re
import unittest

from anonymizer import markdown
from anonymizer.extract import Block, Document, Page, Skipped
from anonymizer.rules import Rules

LONG = ("Soud po provedeném dokazování zjistil, že smlouva byla uzavřena řádně a že žalovaný své povinnosti "
        "nesplnil ani po opakované výzvě, proto bylo rozhodnuto tak, jak je uvedeno ve výroku tohoto rozsudku, "
        "který nabývá právní moci uplynutím lhůty.")


def doc(index, fmt, *pages, **extra):
    """pages: lists of Block, or (number, [Block])."""
    built = []
    for number, page in enumerate(pages, 1):
        built.append(Page(*page) if isinstance(page, tuple) else Page(number, page))
    return Document(index, fmt, built, extra.pop("headers", []), extra.pop("footers", []), **extra)


def p(text, ocr=False):
    return Block("p", text=text, ocr=ocr)


def run(documents, **kwargs):
    rules = Rules()
    markdown.collect(documents, rules)
    return markdown.render(documents, rules, **kwargs)


def body(output):
    return output.text


class MarkdownTests(unittest.TestCase):
    def test_header_footer_and_page_numbers_once(self):
        pages = []
        for n in range(1, 5):
            pages.append((n, [p("Okresní soud v Příkladu\nSpis. zn. 5 C 12/2026"), p(f"Odstavec číslo {n + 10} s jedinečným textem."),
                              p(f"Soudní oddělení, strana {n}"), p(f"Strana {n} z 4")]))
        out = run([doc(1, "PDF", *pages)])
        self.assertEqual(out.text.count("Okresní soud v Příkladu"), 1)
        self.assertIn("Záhlaví: Okresní soud v Příkladu", out.text)
        self.assertEqual(out.text.count("Soudní oddělení"), 1)
        self.assertIn("Zápatí: Soudní oddělení,", out.text)
        self.assertNotIn("Strana 2 z 4", out.text)
        for n in range(1, 5):
            self.assertIn(f"[s. {n}]", out.text)
            self.assertIn(f"Odstavec číslo {n + 10} s jedinečným textem.", out.text)
        self.assertIn("## D1 (PDF, 4 s.)", out.text)

    def test_lines_differing_in_digits_are_not_repeats(self):
        pages = [(n, [p(f"Bod {n + 20} rozsudku"), p("Společný text")]) for n in range(1, 5)]
        out = run([doc(1, "PDF", *pages)])
        for n in range(1, 5):
            self.assertIn(f"Bod {n + 20} rozsudku", out.text)

    def test_sentence_with_page_number_is_kept(self):
        pages = [(i, [p("Okresní soud v Kolíně\nsp. zn. 12 C 345/2026"),
                      p(f"Wolf na straně {i} opět mlčel a soud pokračoval."), p(f"strana {i} z 4")])
                 for i in range(1, 5)]
        out = run([doc(1, "PDF", *pages)])
        for i in range(1, 5):
            self.assertIn(f"na straně {i} opět mlčel a soud pokračoval.", out.text)
        self.assertEqual(out.text.count("Okresní soud v Kolíně"), 1)
        self.assertNotIn("z 4", out.text)

    def test_page_number_forms_removed(self):
        pages = [(n, [p(f"Text {n + 10}."), p(form.format(n))]) for n, form in
                 enumerate(["- {} -", "{}/9", "str. {}"], 1)]
        out = run([doc(1, "PDF", *pages)])
        self.assertNotRegex(out.text, r"- 1 -|1/9|str\. 3")
        self.assertIn("Text 13.", out.text)

    def test_extraction_headers_and_footers_once(self):
        out = run([doc(1, "DOCX", [p("Obsah dokumentu.")], headers=["Hlavička firmy"], footers=["Patička firmy"])])
        self.assertIn("Záhlaví: Hlavička firmy", out.text)
        self.assertIn("Zápatí: Patička firmy", out.text)

    def test_duplicate_document(self):
        first = doc(3, "DOCX", [p("Stejný text dokumentu. Žalovaný Jan Novák.")])
        second = doc(7, "PDF", (1, [p("Stejný   text dokumentu.\nŽalovaný Jan Novák.")]))
        second.pages[0].blocks[0].text = "Stejný text dokumentu. Žalovaný Jan Novák."
        out = run([first, second])
        self.assertIn("D7 = shodný s D3", out.text)
        self.assertEqual(out.text.count("Stejný text dokumentu"), 1)

    def test_repeated_paragraph_references(self):
        docs = [doc(i, "DOCX", [p(f"Úvod {i}."), p(f"Jiný {i}."), p(f"Třetí {i}."), p(LONG)]) for i in (1, 3, 4)]
        docs[0].pages[0].blocks.insert(0, p("Nejprve to."))
        out = run(docs)
        self.assertEqual(out.text.count("tohoto rozsudku"), 1)
        self.assertIn(f"§5 {LONG}", out.text)
        self.assertEqual(out.text.count("[shodné s D1 §5]"), 2)
        self.assertNotIn("§", out.text.split("## D3")[0].replace("§5 ", "", 1))

    def test_unreferenced_paragraph_has_no_prefix(self):
        out = run([doc(1, "DOCX", [p(LONG), p("Kratší odstavec.")])])
        self.assertNotIn("§", out.text)

    def test_short_paragraph_not_deduplicated(self):
        out = run([doc(1, "DOCX", [p("Krátký odstavec.")]), doc(2, "DOCX", [p("Krátký odstavec.")])])
        self.assertNotIn("shodné s D1 §", out.text)

    def test_quoted_email_replaced_when_original_exists(self):
        original = "Dobrý den, posílám podklady ke spisu a prosím o jejich včasné potvrzení do konce týdne."
        mail1 = doc(2, "EML", [Block("mailheader", text="Od: a@example.cz"), p(original)])
        mail2 = doc(5, "EML", [Block("mailheader", text="Od: b@example.cz"), p("Potvrzuji přijetí."),
                               p("> " + original[:48] + "\n> " + original[48:])])
        out = run([mail1, mail2])
        self.assertIn("[citace: D2]", out.text)
        self.assertEqual(out.text.count("posílám podklady"), 1)
        self.assertIn("Potvrzuji přijetí.", out.text)

    def test_quote_after_marker_replaced(self):
        original = "Dobrý den, posílám podklady ke spisu a prosím o jejich včasné potvrzení do konce týdne."
        mail1 = doc(1, "EML", [p(original)])
        mail2 = doc(2, "EML", [p("Souhlasím.\n-----Původní zpráva-----\nOd: x@example.cz\nPředmět: Spis\n\n" + original)])
        # a marker block followed by a body block
        mail2 = doc(2, "EML", [p("Souhlasím."), p("-----Původní zpráva-----\nOd: x@example.cz\nPředmět: Spis"), p(original)])
        out = run([mail1, mail2])
        self.assertIn("[citace: D1]", out.text)
        self.assertEqual(out.text.count("posílám podklady"), 1)
        self.assertNotIn("Původní zpráva", out.text)

    def test_quote_without_original_is_kept(self):
        mail = doc(1, "EML", [p("Odpověď."), p("> Tohle je citace zprávy, jejíž originál ve spisu není, a je dost dlouhá.")])
        out = run([mail])
        self.assertIn("Tohle je citace zprávy", out.text)
        self.assertNotIn("[citace", out.text)

    def test_hyphenated_name_over_lines_is_replaced(self):
        page = [p("Navrhovatelka Marie Nováko-\nvá, bytem Dlouhá 12,\nPraha 1, podala návrh.")]
        out = run([doc(1, "PDF", (1, page))])
        self.assertNotIn("Nováková", out.text)
        self.assertNotIn("Nováko", out.text)
        self.assertRegex(out.text, r"Navrhovatelka \[O\d+\]")
        self.assertIn("podala návrh.", out.text)

    def test_text_layer_lines_joined(self):
        out = run([doc(1, "PDF", (1, [p("první řádek\ndruhý řádek")]))])
        self.assertIn("první řádek druhý řádek", out.text)

    def test_short_labels_and_legend(self):
        self.assertEqual(markdown.short_labels("[OSOBA-12] [ADRESA-1] [RC-3] [OSOBNI_UDAJ-4] [PRIJMENI-5] [ZVLAST-2]"),
                         "[O12] [A1] [RC3] [X4] [O5] [ZV2]")
        out = run([doc(1, "DOCX", [p("Žalovaný Jan Novák, tel. 603 123 456.")])])
        self.assertRegex(out.text, r"\[O1\]")
        self.assertRegex(out.text, r"\[T1\]")
        self.assertNotIn("[OSOBA-", out.text)
        legend = out.text.split("## Legenda\n")[1].split("\n")[0]
        self.assertIn("[O…] osoba", legend)
        self.assertIn("[T…] telefon", legend)
        self.assertNotIn("adresa", legend)

    def test_same_person_same_label_across_documents(self):
        docs = [doc(1, "DOCX", [p("Žalovaný Jan Novák podal odvolání.")]),
                doc(3, "DOCX", [p("Soud vyslechl Jana Nováka."), p("Žalobce Petr Svoboda.")])]
        out = run(docs)
        self.assertIn("Žalovaný [O1] podal", out.text)
        self.assertIn("Soud vyslechl [O1]", out.text)
        self.assertNotIn("Novák", out.text)

    def test_two_phase_earlier_document_uses_later_registration(self):
        first = doc(1, "DOCX", [p("Soud vyslechl Jana Nováka.")])
        second = doc(2, "DOCX", [p("Žalovaný Jan Novák podal odvolání.")])
        rules = Rules()
        markdown.collect([first, second], rules)
        out = markdown.render([first, second], rules)
        self.assertNotIn("Novák", out.text)
        self.assertEqual(len(set(re.findall(r"\[O\d+\]", out.text))), 1)

    def test_tables_compact(self):
        table = Block("table", rows=[["Pole", "Věk", ""], ["Jan", "40", ""], ["a|b", "x\ny", ""]])
        out = run([doc(1, "DOCX", [table])])
        self.assertIn("|Pole|Věk|\n", out.text)
        self.assertIn("|a\\|b|x / y|", out.text)
        self.assertNotIn("---", out.text)
        self.assertNotIn("| ", out.text)

    def test_table_cells_labelled_by_header(self):
        table = Block("table", rows=[["Jméno a příjmení", "Telefon"], ["Jan Novák", "603 123 456"]])
        out = run([doc(1, "DOCX", [table])])
        self.assertNotIn("Novák", out.text)
        self.assertNotIn("603", out.text)
        self.assertIn("|[O1]|[T1]|", out.text)

    def test_form_table_as_label_value_lines(self):
        table = Block("table", rows=[["Jméno a příjmení:", "Jan Novák"], ["Telefon:", "603 123 456"]])
        out = run([doc(1, "DOCX", [table])])
        self.assertIn("Jméno a příjmení: [O1]", out.text)
        self.assertIn("Telefon: [T1]", out.text)

    def test_two_column_colon_table(self):
        table = Block("table", rows=[["Věc:", "Žaloba"], ["Soud:", "Okresní"]])
        out = run([doc(1, "DOCX", [table])])
        self.assertIn("Věc: Žaloba\nSoud: Okresní", out.text)

    def test_images_notes_and_mail_header(self):
        blocks = [Block("image"), Block("image"), Block("image"), p("Text."),
                  Block("image", text="Text na obrázku", ocr=True), Block("note", text="Poznámka pod čarou")]
        out = run([doc(1, "DOCX", blocks), doc(2, "EML", [Block("mailheader", text="Od: x\nKomu: y")])])
        self.assertIn("[OBRÁZEK VYNECHÁN ×3]", out.text)
        self.assertIn("[OBRÁZEK, text OCR:] Text na obrázku", out.text)
        self.assertIn("[pozn.] Poznámka pod čarou", out.text)
        self.assertIn("Od: x\nKomu: y", out.text)

    def test_paragraphs_separated_by_single_newline(self):
        out = run([doc(1, "DOCX", [p("Jedna."), p("Dvě.")])])
        self.assertIn("Jedna.\nDvě.", out.text)
        self.assertNotIn("\n\n", out.text)

    def test_contents_dates_attachments_skipped(self):
        docs = [doc(1, "DOCX", [p("Smlouva ze dne 5. 3. 2026 byla uzavřena.")]),
                doc(4, "PDF", (1, [p("Text.")]), (2, [p("Další.")]), parent=1),
                doc(6, "EML", [p("Zpráva.")], date="2026-02-01")]
        out = run(docs + [Skipped(5, "ZIP", "archive_encrypted")])
        self.assertRegex(out.text, r"D1 DOCX, 1 s., 2026-03-05, ~\d+ tok\.")
        self.assertRegex(out.text, r"D4 PDF, 2 s\., příloha D1, ~\d+ tok\.")
        self.assertRegex(out.text, r"D6 EML, 1 s., 2026-02-01, ~\d+ tok\.")
        self.assertIn("## Nezpracované položky\npoložka 5: archive_encrypted", out.text)
        self.assertEqual(out.skipped, [(5, "archive_encrypted")])
        self.assertEqual(out.documents, 3)
        self.assertNotIn("ZIP", out.text)
        self.assertRegex(out.text, r"Celkem ~[\d ]+ tokenů")

    def test_version_in_header(self):
        out = run([doc(1, "DOCX", [p("Text.")])], version="9.9")
        self.assertIn("SpisRedakt 9.9. Automatická anonymizace", out.text)

    def test_split_mode(self):
        docs = [doc(1, "DOCX", [p("Úvod."), p(LONG)]), doc(2, "DOCX", [p(LONG)])]
        out = run(docs, split=True)
        self.assertEqual(sorted(out.files), ["00_obsah.md", "D0001.md", "D0002.md"])
        self.assertEqual(out.text, out.files["00_obsah.md"])
        self.assertIn("## Obsah", out.text)
        self.assertNotIn("tohoto rozsudku", out.text)
        self.assertIn(f"§2 {LONG}", out.files["D0001.md"])
        self.assertIn("[shodné s D1 §2]", out.files["D0002.md"])
        self.assertTrue(out.files["D0001.md"].startswith("## D1 (DOCX, 1 s.)"))

    def test_no_unique_paragraph_lost(self):
        docs = []
        unique = []
        for i in range(1, 5):
            paragraphs = [f"Jedinečný odstavec {i}-{j}, žalovaný Jan Novák, tel. 603 123 45{j}." for j in range(3)]
            unique += paragraphs
            docs.append(doc(i, "DOCX", [p(t) for t in paragraphs] + [p(LONG), Block("note", text=f"Pozn. {i}")]))
        pdf_pages = [(n, [p("Hlavička spisu"), p(f"Svědek mlčel na straně {n}."), p(f"Obsah strany {n + 10}, jedinečný."), p(f"Strana {n} z 3")]) for n in (1, 2, 3)]
        docs.append(doc(9, "PDF", *pdf_pages))
        rules = Rules()
        markdown.collect(docs, rules)
        out = markdown.render(docs, rules)
        text = markdown.short_labels(out.text)
        flat = " ".join(text.split())
        expected = []
        probe = Rules()
        for t in unique:
            expected.append(" ".join(markdown.short_labels(rules.replace(t)[0]).split()))
        expected += [f"Svědek mlčel na straně {n}." for n in (1, 2, 3)]
        expected += [f"Obsah strany {n + 10}, jedinečný." for n in (1, 2, 3)] + ["Hlavička spisu"]
        for item in expected:
            self.assertIn(item, flat)
        self.assertEqual(flat.count("Hlavička spisu"), 1)
        self.assertEqual(flat.count("tohoto rozsudku"), 1)
        del probe

    def test_no_file_names_in_output(self):
        out = run([doc(1, "DOCX", [p("Text.")]), Skipped(2, "PDF", "pdf_encrypted_or_empty")], split=True)
        for content in [out.text, *out.files.values()]:
            self.assertNotRegex(content, r"\.(docx|pdf|eml|msg|xlsx|zip)\b")
            self.assertNotIn("/", content.replace("znaky / 3", ""))

    def test_collect_has_no_side_effect_on_documents(self):
        document = doc(1, "PDF", (1, [p("Žalovaný Jan Novák\npodal odvolání.")]))
        run([document])
        self.assertEqual(document.pages[0].blocks[0].text, "Žalovaný Jan Novák\npodal odvolání.")

    def test_tokens_after_lower_than_before(self):
        pages = [(n, [p("Okresní soud v Příkladu"), p(LONG), p(f"Text strany {n}."), p(f"Strana {n} z 5")])
                 for n in range(1, 6)]
        docs = [doc(1, "PDF", *pages), doc(2, "PDF", *pages),
                doc(3, "DOCX", [Block("table", rows=[["Jméno", "Telefon"], ["Jan Novák", "603 123 456"]]), p(LONG)])]
        out = run(docs)
        self.assertGreater(out.tokens_before, 0)
        self.assertLess(out.tokens_after, out.tokens_before)

    def test_estimate_tokens(self):
        self.assertEqual(markdown.estimate_tokens("abcd"), 2)
        self.assertEqual(markdown.estimate_tokens(""), 0)


if __name__ == "__main__":
    unittest.main()
