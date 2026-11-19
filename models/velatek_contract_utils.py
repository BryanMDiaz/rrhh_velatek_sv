from datetime import date
from dateutil.relativedelta import relativedelta

# Unidades, decenas y centenas en español
UNIDADES = ["", "un", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve"]
DECENAS = ["", "diez", "veinte", "treinta", "cuarenta", "cincuenta", "sesenta", "setenta", "ochenta", "noventa"]
DIECIS = ["diez", "once", "doce", "trece", "catorce", "quince", "dieciséis", "diecisiete", "dieciocho", "diecinueve"]
VEINTIS = ["veinte", "veintiún", "veintidós", "veintitrés", "veinticuatro", "veinticinco", "veintiséis", "veintisiete", "veintiocho", "veintinueve"]
CENTENAS = ["", "ciento", "doscientos", "trescientos", "cuatrocientos", "quinientos", "seiscientos", "setecientos", "ochocientos", "novecientos"]
MESES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

def _get_centenas(numero):
    if numero == 100:
        return "cien"
    
    centena = numero // 100
    resto = numero % 100
    
    texto = CENTENAS[centena]
    if resto > 0:
        texto += " " + _get_decenas(resto)
    return texto.strip()

def _get_decenas(numero):
    if numero < 10:
        return UNIDADES[numero]
    elif numero < 20:
        return DIECIS[numero - 10]
    elif numero < 30:
        return VEINTIS[numero - 20]
    
    decena = numero // 10
    unidad = numero % 10
    
    texto = DECENAS[decena]
    if unidad > 0:
        texto += " y " + UNIDADES[unidad]
    return texto

def numero_a_letras(numero):
    if numero == 0:
        return "cero"
    
    texto = ""
    millones = numero // 1000000
    resto_millones = numero % 1000000
    
    if millones > 0:
        if millones == 1:
            texto += "un millón "
        else:
            texto += _get_centenas(millones) + " millones "
            
    miles = resto_millones // 1000
    resto_miles = resto_millones % 1000
    
    if miles > 0:
        if miles == 1:
            texto += "mil "
        else:
            texto += _get_centenas(miles) + " mil "
            
    if resto_miles > 0:
        texto += _get_centenas(resto_miles)
        
    return texto.strip()

def monto_a_letras(monto):
    entero = int(monto)
    decimal = int(round((monto - entero) * 100))
    
    texto_entero = numero_a_letras(entero).upper()
    
    if entero == 1:
        texto_entero = "UN DÓLAR"
    else:
        texto_entero += " DÓLARES"
        
    texto_entero += f" CON {decimal:02d}/100"
    return texto_entero

def fecha_a_letras(fecha):
    if not fecha:
        return ""
    dia = numero_a_letras(fecha.day)
    mes = MESES[fecha.month]
    anio = numero_a_letras(fecha.year)
    return f"{dia} de {mes} de {anio}".lower()

def calcular_edad(fecha_nacimiento, fecha_base=None):
    if not fecha_nacimiento:
        return 0
    if not fecha_base:
        fecha_base = date.today()
    return relativedelta(fecha_base, fecha_nacimiento).years
