## Crosswalk
### El script crosswalk_cli.py recibe tres parámetros:

  - CSV de entrada
  - Configuración de mapeo
  - CSV destino
  
### JSON de configuración

El archivo de configuracion para el mapeo de metadatos debe estar en formato JSON.

Ejemplo:

```json 
 [
    [
        {
            "left":"dc.date.issued+dc.date.issued[]+dc.date.issued[es]+dc.date[]+dc.date[es]",
            "replace":"date",
            "default":"",
            "required":false,
            "filter":""
        },
     ],
     {
        "original_separator":"||",
        "replace_separator":"|",
        "file_delimiter":","
    }
 ]
```
 
La clave *left* define la o las columnas del CSV de entrada que van a ser mapeadas a la columna definida en la clave *replace*. La clave *default* define el valor que toma por defecto la columna (en caso de no tener un valor presente), *required* determina si es obligatorio que el campo tenga un valor original para mantener la fila, y *filter* define los filtros a aplicar sobre el dato. Los filtros existentes actualmente son trim y lowercase, pueden utilizarse ambos separandose por '|'.

### Ejecutar en consola:

python crosswalk_cli.py input.csv config.json output.csv
