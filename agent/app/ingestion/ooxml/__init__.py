from .block import OoxmlBlock
from .. import register

register("docx", OoxmlBlock())
register("ooxml", OoxmlBlock())
