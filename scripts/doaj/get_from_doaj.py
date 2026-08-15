import requests
import csv
import time

def obtener_articulos_unlp_csv(output_filename="articulos_unlp_doaj_v2.csv"):
    # -------------------------------------------------------------
    # PASO 1: Obtener la lista de ISSNs de journal_titles de la UNLP
    # -------------------------------------------------------------
    print("Paso 1: Buscando journal_titles de la UNLP para extraer sus ISSNs...")
    query_journals = 'bibjson.publisher.name.exact:"Universidad Nacional de La Plata"'
    url_journals = f"https://doaj.org/api/search/journals/{query_journals}?pageSize=100"
    
    resp_journals = requests.get(url_journals)
    if resp_journals.status_code != 200:
        print(f"Error al consultar journal_titles: {resp_journals.status_code}")
        return

    journals_data = resp_journals.json().get("results", [])
    issns = set()

    for item in journals_data:
        bib = item.get("bibjson", {})
        if bib.get("eissn"):
            issns.add(bib.get("eissn"))
        if bib.get("pissn"):
            issns.add(bib.get("pissn"))

    if not issns:
        print("No se encontraron ISSNs para las journal_titles de la UNLP.")
        return

    print(f"Se encontraron {len(journals_data)} journal_titles con {len(issns)} ISSNs únicos.")

    # -------------------------------------------------------------
    # PASO 2: Buscar todos los artículos asociados a esos ISSNs
    # -------------------------------------------------------------
    print("\nPaso 2: Descargando artículos asociados a esos ISSNs...")
    
    # Construimos la consulta OR para Lucene / Elasticsearch: (ISSN1 OR ISSN2 OR ...)
    issn_query_str = " OR ".join([f'"{issn}"' for issn in issns])
    articles_query = f"issn:{issn_query_str}"
    
    base_url_articles = "https://doaj.org/api/search/articles/"
    page_size = 100
    page = 1
    all_articles = []

    while True:
        url = f"{base_url_articles}{articles_query}?page={page}&pageSize={page_size}"
        resp_articles = requests.get(url)
        
        if resp_articles.status_code != 200:
            print(f"Error al consultar página {page} de artículos: {resp_articles.status_code}")
            break
            
        data = resp_articles.json()
        results = data.get("results", [])
        total = data.get("total", 0)
        
        if not results:
            break

        for item in results:
            bib = item.get("bibjson", {})
            
            title = bib.get("title", "")
            
            # Datos de la journal_title
            journal_info = bib.get("journal", {})
            journal_title = journal_info.get("title", "")
            volume = journal_info.get("volume", "")
            number = journal_info.get("number", "")
            
            year = bib.get("year", "")
            month = bib.get("month", "")
            month_num = ""
            
            # Convertir nombre de mes a número
            month_map = {
                'enero': '01', 'febrero': '02', 'marzo': '03', 'abril': '04',
                'mayo': '05', 'junio': '06', 'julio': '07', 'agosto': '08',
                'septiembre': '09', 'octubre': '10', 'noviembre': '11', 'diciembre': '12'
            }
            if month.lower() in month_map:
                month_num = month_map[month.lower()]
            elif month:
                try:
                    month_num = f"{int(month):02d}"
                except ValueError:
                    month_num = month
            
            # authors
            authors_list = bib.get("author", [])
            authors = "; ".join([a.get("name", "") for a in authors_list if "name" in a])
            
            # doi
            identifiers = bib.get("identifier", [])
            doi = next((i.get("id", "") for i in identifiers if i.get("type") == "doi"), "")
            
            # url_article
            links = bib.get("link", [])
            url_article = ""
            for l in links:
                if l.get("type") == "fulltext":
                    url_article = l.get("url", "")
                    break
            if not url_article and links:
                url_article = links[0].get("url", "")

            all_articles.append({
                "title": title,
                "authors": authors,
                "journal_title": journal_title,
                "date": year + "-" + month_num,
                "volume": volume,
                "number": number,
                "doi": doi,
                "url_article": url_article,
                "type": "Articulo",
                "id_doaj": item.get("id", "")
            })

        print(f"Procesada página {page} - {len(all_articles)} de {total} artículos recolectados.")
        
        if len(all_articles) >= total or len(results) < page_size:
            break
            
        page += 1
        time.sleep(0.3)

    # -------------------------------------------------------------
    # PASO 3: Guardar los resultados en CSV
    # -------------------------------------------------------------
    if all_articles:
        fields = ["title", "authors", "journal_title", "date", "volume", "number", "type", "doi", "url_article", "id_doaj"]
        with open(output_filename, mode="w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(all_articles)
            
        print(f"\n¡Proceso completado! Se exportaron {len(all_articles)} artículos a '{output_filename}'.")
    else:
        print("No se encontraron artículos para los ISSNs especificados.")

if __name__ == "__main__":
    obtener_articulos_unlp_csv()