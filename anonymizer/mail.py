"""Redact e-mail messages (EML) and convert Outlook messages (MSG) to redacted EML."""
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import format_datetime
from datetime import datetime, timedelta, timezone
import re

from lxml import html as lxml_html

ADDRESS_HEADERS = ("From", "Sender", "Reply-To", "To", "Cc", "Bcc")
DISABLED_LINK = "https://example.invalid/"
PLACEHOLDER = "anonymizovano.invalid"
BLOCK_TAGS = {"p", "div", "td", "th", "li", "tr", "table", "h1", "h2", "h3", "h4", "h5", "h6", "br",
              "blockquote", "pre", "body", "ul", "ol", "dl", "dt", "dd", "hr", "section", "article"}
DROP_TAGS = {"script", "iframe", "object", "embed", "link", "meta", "base", "form", "noscript", "applet"}
MAX_DEPTH = 5


class Mail:
    def __init__(self, rules, tessdata, redact_attachment, redact_image):
        self.rules, self.tessdata = rules, tessdata
        self.redact_attachment, self.redact_image = redact_attachment, redact_image
        self.attachments = 0
        self.images = 0

    # -- headers -----------------------------------------------------------
    def address_header(self, header):
        values = []
        for address in getattr(header, "addresses", ()):
            email = self.rules.cell("EMAIL", address.addr_spec) if address.addr_spec else None
            name = self.rules.cell("OSOBA", address.display_name) if address.display_name else None
            # Readers need a valid address: "[EMAIL-1]" becomes email-1@anonymizovano.invalid.
            if email:
                email = email.strip("[]").casefold() + "@" + PLACEHOLDER
            if name:
                name = f'"{name}"'
            values.append(f"{name} <{email}>" if name and email else name or (f"<{email}>" if email else ""))
        return ", ".join(value for value in values if value)

    def headers(self, source):
        """Return the kept headers; all others (Received, Message-ID, X-...) are dropped."""
        values = []
        for name in ADDRESS_HEADERS:
            if source[name] is not None:
                value = self.address_header(source[name])
                if value:
                    values.append((name, value))
        if source["Subject"] is not None:
            values.append(("Subject", self.rules.replace(str(source["Subject"]))[0]))
        if source["Date"] is not None:
            values.append(("Date", str(source["Date"])))
        return values

    # -- bodies --------------------------------------------------------------
    def redact_html(self, markup, cids):
        document = lxml_html.document_fromstring(markup or "<html></html>")
        for node in list(document.iter()):
            if isinstance(node.tag, str) and node.tag.lower() in DROP_TAGS:
                node.drop_tree()
        for node in document.iter():
            if not isinstance(node.tag, str):
                continue
            for attribute in list(node.attrib):
                value = node.get(attribute)
                lowered = attribute.lower()
                if lowered.startswith("on"):
                    del node.attrib[attribute]
                elif lowered == "href":
                    node.set(attribute, DISABLED_LINK if not value.startswith("#") else value)
                elif lowered in {"src", "background", "srcset", "poster"}:
                    reference = value[4:].strip("<>") if value.lower().startswith("cid:") else None
                    if reference and reference in cids:
                        node.set(attribute, "cid:" + cids[reference])
                    else:
                        # Remote content would reveal the reader and may hold identifiers.
                        del node.attrib[attribute]
                elif lowered in {"alt", "title", "value", "placeholder", "aria-label"}:
                    node.set(attribute, self.rules.replace(value)[0])
        blocks, current = [], []

        def visit(node):
            nonlocal current
            block = isinstance(node.tag, str) and node.tag.lower() in BLOCK_TAGS
            if block:
                blocks.append(current)
                current = []
            current.append((node, "text"))
            for child in node:
                if isinstance(child.tag, str):
                    visit(child)
                current.append((child, "tail"))
            if block:
                blocks.append(current)
                current = []

        visit(document)
        blocks.append(current)
        for parts in blocks:
            self.redact_parts(parts)
        output = lxml_html.tostring(document, encoding="unicode", doctype="<!DOCTYPE html>")
        for parts in blocks:
            if self.rules.find("".join(getattr(node, attr) or "" for node, attr in parts)):
                raise ValueError("Ve výstupu HTML e-mailu zůstal detekovatelný text.")
        return output

    def redact_parts(self, parts):
        values = [getattr(node, attr) or "" for node, attr in parts]
        text = "".join(values)
        offsets, end = [], 0
        for value in values:
            offsets.append(end)
            end += len(value)
        for match in reversed(self.rules.find(text)):
            label = self.rules.label(match)
            for index, offset in enumerate(offsets):
                left, right = max(match.start - offset, 0), min(match.end - offset, len(values[index]))
                if right <= left:
                    continue
                values[index] = values[index][:left] + label + values[index][right:]
                setattr(parts[index][0], parts[index][1], values[index])
                label = ""

    # -- structure -------------------------------------------------------------
    def attachment_name(self, suffix):
        self.attachments += 1
        return f"priloha-{self.attachments:04d}{suffix}"

    def part(self, source, cids, depth):
        content_type = source.get_content_type()
        maintype, subtype = content_type.split("/", 1)
        target = EmailMessage()
        # message/rfc822 also reports is_multipart(), so it is handled first.
        if content_type == "message/rfc822":
            if depth >= MAX_DEPTH:
                raise ValueError("E-mail má příliš mnoho vnořených zpráv.")
            target.set_content(self.message(source.get_content(), depth + 1))
            return target
        if source.is_multipart():
            if content_type in {"multipart/encrypted"}:
                raise ValueError("Zašifrovaný e-mail nelze zkontrolovat.")
            children = [child for child in source.iter_parts()
                        if child.get_content_type() not in {"application/pkcs7-signature",
                                                            "application/x-pkcs7-signature",
                                                            "application/pgp-signature"}]
            for child in children:
                cid = child["Content-ID"]
                if cid:
                    self.images += 1
                    cids[str(cid).strip().strip("<>")] = f"obrazek-{self.images:04d}@spisredakt.invalid"
            if subtype in {"alternative", "related"}:
                getattr(target, f"make_{subtype}")()
            else:
                target.make_mixed()
            for child in children:
                target.attach(self.part(child, cids, depth))
            return target
        if content_type in {"application/pkcs7-mime", "application/x-pkcs7-mime", "application/pgp-encrypted"}:
            raise ValueError("Zašifrovaný nebo podepsaný obsah e-mailu nelze zkontrolovat.")
        disposition = source.get_content_disposition()
        filename = source.get_filename() or ""
        if maintype == "text" and subtype in {"plain", "html"} and disposition != "attachment":
            body = source.get_content()
            if subtype == "plain":
                body = self.rules.replace(body)[0]
                if self.rules.find(body):
                    raise ValueError("Ve výstupu e-mailu zůstal detekovatelný text.")
            else:
                body = self.redact_html(body, cids)
            target.set_content(body, subtype=subtype, charset="utf-8")
            return target
        data = source.get_payload(decode=True) or b""
        cid = source["Content-ID"]
        if cid and maintype == "image" and subtype in {"png", "jpeg", "jpg"}:
            data = self.redact_image(data, "png" if subtype == "png" else "jpg", self.rules, self.tessdata)
            reference = cids.get(str(cid).strip().strip("<>"), "obrazek@spisredakt.invalid")
            target.set_content(data, maintype, subtype, disposition="inline", cid=f"<{reference}>")
            return target
        suffix = re.search(r"\.[A-Za-z0-9]{1,5}$", filename)
        data, released = self.redact_attachment(data, suffix[0].lower() if suffix else "")
        target.set_content(data, "application", "octet-stream", disposition="attachment",
                           filename=self.attachment_name(released))
        return target

    def message(self, source, depth=0):
        headers = self.headers(source)
        target = self.part(source, {}, depth)
        for name, value in headers:
            target[name] = value
        if target["MIME-Version"] is None:
            target["MIME-Version"] = "1.0"
        return target


def check(message, rules):
    from .czech import LABEL

    for name in ADDRESS_HEADERS:
        for address in getattr(message[name], "addresses", ()):
            if (address.addr_spec and not address.addr_spec.endswith("@" + PLACEHOLDER)
                    or address.display_name and not LABEL.fullmatch(address.display_name)):
                raise ValueError("Kontrola adres e-mailu našla zbývající údaj.")
    for name in message.keys():
        value = str(message[name])
        if name in ADDRESS_HEADERS:
            continue
        if name.lower() not in {"content-type", "content-transfer-encoding", "content-disposition",
                                "content-id", "mime-version", "date"} and rules.find(value):
            raise ValueError("Kontrola hlavičky e-mailu našla zbývající údaj.")
    for part in message.walk():
        if part.get_content_type() == "text/plain" and part.get_content_disposition() != "attachment":
            if rules.find(part.get_content()):
                raise ValueError("Kontrola e-mailu našla zbývající údaj.")


def redact_eml(data, rules, tessdata, redact_attachment, redact_image):
    source = BytesParser(policy=policy.default).parsebytes(data)
    if not source.keys():
        raise ValueError("Soubor není platný e-mail.")
    message = Mail(rules, tessdata, redact_attachment, redact_image).message(source)
    output = message.as_bytes(policy=policy.SMTP)
    check(BytesParser(policy=policy.default).parsebytes(output), rules)
    return output


# -- Outlook MSG ---------------------------------------------------------------

def msg_to_eml(data):
    """Convert an Outlook MSG file to an EmailMessage with the visible content."""
    import io
    import olefile

    if not olefile.isOleFile(io.BytesIO(data)):
        raise ValueError("Soubor není platná zpráva Outlook MSG.")
    with olefile.OleFileIO(io.BytesIO(data)) as ole:
        def stream(path):
            return ole.openstream(path).read() if ole.exists(path) else None

        def string(prefix, tag):
            value = stream(f"{prefix}__substg1.0_{tag}001F")
            if value is not None:
                return value.decode("utf-16-le", "replace").rstrip("\0")
            value = stream(f"{prefix}__substg1.0_{tag}001E")
            return value.decode("cp1250", "replace").rstrip("\0") if value is not None else ""

        def properties(path, header):
            raw = stream(path) or b""
            values = {}
            for offset in range(header, len(raw) - 15, 16):
                tag = int.from_bytes(raw[offset:offset + 4], "little")
                values[tag] = raw[offset + 8:offset + 16]
            return values

        message = EmailMessage()
        sender = string("", "0C1A")
        address = string("", "0C1F") or string("", "5D01")
        if sender or address:
            message["From"] = f'"{sender}" <{address}>' if address and "@" in address else f'"{sender}"'
        recipients = {1: [], 2: [], 3: []}
        for entry in ole.listdir(storages=True, streams=False):
            if len(entry) == 1 and entry[0].startswith("__recip_version1.0_"):
                prefix = entry[0] + "/"
                kind = int.from_bytes(properties(prefix + "__properties_version1.0", 8).get(0x0C150003, b"\1")[:4], "little")
                name, email = string(prefix, "3001"), string(prefix, "39FE") or string(prefix, "3003")
                recipients.setdefault(kind, []).append(f'"{name}" <{email}>' if "@" in email else f'"{name}"')
        for kind, header in ((1, "To"), (2, "Cc"), (3, "Bcc")):
            if recipients.get(kind):
                message[header] = ", ".join(recipients[kind])
        subject = string("", "0037")
        if subject:
            message["Subject"] = subject
        top = properties("__properties_version1.0", 32)
        stamp = top.get(0x00390040) or top.get(0x0E060040)
        if stamp:
            ticks = int.from_bytes(stamp, "little")
            moment = datetime(1601, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=ticks // 10)
            message["Date"] = format_datetime(moment)
        body = string("", "1000")
        html = stream("__substg1.0_10130102")
        if not body and not html:
            raise ValueError("Zpráva MSG nemá textové tělo. Uložte ji z Outlooku jako EML nebo PDF.")
        message.set_content(body or "")
        if html:
            charset = "utf-8"
            declared = re.search(rb"charset=[\"']?([\w-]+)", html[:4096])
            if declared:
                charset = declared[1].decode("ascii")
            try:
                markup = html.decode(charset)
            except (LookupError, UnicodeDecodeError):
                markup = html.decode("cp1250", "replace")
            message.add_alternative(markup, subtype="html")
        for entry in ole.listdir(storages=True, streams=False):
            if len(entry) != 1 or not entry[0].startswith("__attach_version1.0_"):
                continue
            prefix = entry[0] + "/"
            if ole.exists(prefix + "__substg1.0_3701000D"):
                raise ValueError("Zpráva MSG obsahuje vloženou zprávu Outlook. Uložte ji samostatně.")
            payload = stream(prefix + "__substg1.0_37010102")
            if payload is None:
                raise ValueError("Příloha zprávy MSG je odkaz nebo objekt, který nelze zkontrolovat.")
            filename = string(prefix, "3707") or string(prefix, "3704") or "priloha.bin"
            cid = string(prefix, "3712")
            mime = (string(prefix, "370E") or "application/octet-stream").lower()
            maintype, _, subtype = mime.partition("/")
            if message.get_content_type() != "multipart/mixed":
                message.make_mixed()
            part = EmailMessage()
            if cid:
                part.set_content(payload, maintype, subtype or "octet-stream", disposition="inline", cid=f"<{cid}>")
            else:
                part.set_content(payload, maintype, subtype or "octet-stream", disposition="attachment",
                                 filename=filename)
            message.attach(part)
        return message


def redact_msg(data, rules, tessdata, redact_attachment, redact_image):
    message = msg_to_eml(data)
    return redact_eml(message.as_bytes(policy=policy.SMTP), rules, tessdata, redact_attachment, redact_image)
