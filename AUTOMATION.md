# Opakované iterace

Workflow Windows spouští syntetické regresní testy a sestavení při změnách, bez přístupu k produkčním dokumentům a bez klíče OpenAI. Artefakt sestavení má platnost 14 dní.

Pro Codex je vhodný oddělený linuxový workflow, nejprve s ručním spuštěním správcem. První úkol může najít chybějící regresní scénáře u OCR, zlepšit jeden test nebo navrhnout jednu malou změnu podle úkolu ve verzovaném backlogu. Po ověření nákladů a výstupů lze přidat týdenní plán.

- Spouštějte nejvýše jeden běh současně, s timeoutem 15 minut, jedním malým úkolem na běh a samostatným API projektem s rozpočtovým upozorněním a omezenou kvótou. Pro tvrdý strop zastavte spouštění po vyčerpání přidělené kvóty.
- Vycházejte z chráněné výchozí větve. Připněte akci na prověřený commit a Codex CLI na verzi. Použijte `contents: read`, checkout bez uložených přihlašovacích údajů, omezený zápis do pracovního adresáře a strategii `drop-sudo`. Windows ponechte jako oddělené ověření sestavení.
- Klíč předávejte přes GitHub Secret jen akci Codex. Ze vstupů vylučte skutečné spisy, jmenné mapy, neprověřené texty issues a změny z forků. Pro krok s klíčem použijte pouze důvěryhodný kód.
- První běhy mohou končit patchem ke kontrole. Pokud bude potřeba automatický návrh PR, nechte samostatný krok s omezenými právy zkontrolovat cesty a velikost patche, založit pracovní větev a otevřít draft PR. Změny workflow, oprávnění a závislostí vyžadují lidskou kontrolu. Slučování podmiňte testy na Windows a schválením.
- Při selhání ponechte artefakt a ukončete běh. Další běh vynechte, dokud se nerozhodne o otevřeném návrhu. Rozšiřujte syntetickou sadu o smíšené skeny, obtížná písma, zalomení řádků a ruční náhled.

[OpenAI – Codex GitHub Action](https://learn.chatgpt.com/docs/github-action) · [GitHub – zabezpečení Actions](https://docs.github.com/en/actions/reference/security/secure-use)

Plánovaný běh Codexu vyžaduje nastavení v GitHubu a API klíč. Tato verze ho pouze popisuje.
