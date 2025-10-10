# Product Scraper & Classifier - Quick Start Guide

## 🔍 Understanding Classification

see `technical_approach.md`

## Quick Start

### Prerequisites
- Python 3.8 or higher
- Internet connection | Google Colab
- `classification_tree.csv` file provided

### Installation

just run the jupyter notebook `products_pipeline.ipynb`

**That's it!** The script will:
1. Scrape products from Sika (from sitemap)
2. Scrape products from Flex-Tools (by crawling)
3. Classify each product using the gemini
4. Export results to `products_output.csv`


### Expected Output

```
INFO - Starting Product Extraction Pipeline
INFO - Fetching Sika sitemap: https://gcc.sika.com/en/products.sitemap.xml
INFO - Found 150 product URLs in sitemap
INFO:logger:Scraping Sika product: https://gcc.sika.com/en/construction/concrete/water-reducers/plasticizers/sika-plastiment-110cx.html
INFO:tornado.access:200 POST /v1beta/models/gemini-2.5-flash-lite:generateContent?%24alt=json%3Benum-encoding%3Dint (127.0.0.1) 9234.92ms
INFO:logger:Classified 'Sika® Plastiment®-110 CX' as: Construction technology > Construction chemicals > Concrete additive > Concrete post-treatment agent
  Confidence: high | Reasoning: The product is explicitly described as a 'water-reducing and retarding concrete admixture'. Option I
INFO:logger:Scraping Sika product: https://gcc.sika.com/en/construction/concrete/water-reducers/non-pce-based-superplasticizers/sikament-nn-s.html
INFO:tornado.access:200 POST /v1beta/models/gemini-2.5-flash-lite:generateContent?%24alt=json%3Benum-encoding%3Dint (127.0.0.1) 1898.12ms
INFO:logger:Classified 'Sikament® NN S' as: Construction technology > Construction chemicals > Concrete additive > Concrete post-treatment agent
  Confidence: high | Reasoning: The product is described as a 'High Performance Plasticising and Slump Retaining Concrete Admixture'
INFO:logger:Scraping Sika product: https://gcc.sika.com/en/construction/concrete/water-reducers/non-pce-based-superplasticizers/sikament-r-4-qv-cdstar.html
INFO:tornado.access:200 POST /v1beta/models/gemini-2.5-flash-lite:generateContent?%24alt=json%3Benum-encoding%3Dint (127.0.0.1) 2148.72ms
INFO:logger:Classified 'Sikament® R-4 QV CD Star' as: Construction technology > Construction chemicals > Concrete additive > Concrete post-treatment agent
  Confidence: high | Reasoning: The product is described as a 'High Range Water-Reducing and Slump Retaining Concrete Admixture', wh

2025-10-08 10:35:31 - INFO - Exported 40 products to products_output.csv
```

## 📊 Output Structure

The script generates `products_output.csv` with the following columns:

| Column | Description | Example |
|--------|-------------|---------|
| brand | Brand name | Sika, Flex |
| product_name | Full product name | SikaWrap®-600 C WV |
| model_article_number | Model/SKU | 600 |
| category | 2nd level category | Electric tool |
| subcategory | 3rd level category | Drill (electrical) |
| type_id | 4th level Type ID | 116752 |
| classification_path | Full numeric path | 115547.116749.116750.116752 |
| technical_specs | JSON of specifications | {"thickness": "0.331mm"} |
| short_description | Brief description | Woven unidirectional... |
| long_description | Detailed description | SikaWrap®-600 C WV is... |
| product_image_url | Image URL | https://... |
| datasheet_url | PDF datasheet URL | https://.../datasheet.pdf |
| source_url | Original product page | https://gcc.sika.com/... |

## ⚙️ Configuration

### Adjust Number of Products
```python
# modify main():
pipeline.run(max_products_per_site=50) # disabled to scrape all products
```

### Change Output Filename
```python
pipeline.export_to_csv('my_products.csv')  # Custom filename
```

### USAGE EXAMPLE WITH SIMILARITY SEARCH
adjust the notebook according to the fit the following:

```python
if __name__ == '__main__':
    import os
    
    print("="*60)
    print("Gemini Classifier with Similarity Search - Demo")
    print("="*60)
    
    GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', 'your-api-key-here')
    
    # Load classification tree
    tree_df = pd.read_csv('classification_tree.csv')
    
    # Create classifier with TF-IDF (fast, no extra dependencies)
    print("\nOption 1: Using TF-IDF similarity (fast)")
    classifier_tfidf = GeminiClassifier(
        GEMINI_API_KEY, 
        tree_df,
        use_sentence_transformers=False
    )
    
    # OR: Create classifier with sentence transformers (better but slower)
    # Requires: pip install sentence-transformers
    # print("\nOption 2: Using Sentence Transformers (better)")
    # classifier_st = GeminiClassifier(
    #     GEMINI_API_KEY, 
    #     tree_df,
    #     use_sentence_transformers=True
    # )
    
    # Test classification
    test_products = [
        {
            'name': 'PXE-80 12 EC Cordless Polisher',
            'description': 'Compact 12V cordless polisher for professional automotive and industrial use',
            'specs': {
                'voltage': '12V',
                'speed': '1200-4500 RPM',
                'weight': '1.2 kg',
                'battery': 'Li-ion',
                'disc_diameter': '80mm'
            }
        },
        {
            'name': 'SikaWrap-600 C WV Carbon Fiber Fabric',
            'description': 'Woven unidirectional carbon fibre fabric for structural strengthening',
            'specs': {
                'material': 'carbon fiber',
                'tensile_strength': '4000 N/mm²',
                'thickness': '0.331 mm',
                'application': 'structural reinforcement'
            }
        }
    ]
    
    print("\n" + "="*60)
    print("Testing Classification with Similarity Search")
    print("="*60)
    
    for i, product in enumerate(test_products, 1):
        print(f"\n--- Test Product {i} ---")
        print(f"Name: {product['name']}")
        print(f"Description: {product['description']}")
        
        # Show simlar categories found
        product_text = f"{product['name']} {product['description']} {' '.join([f'{k} {v}' for k, v in product['specs'].items()])}"
        similar_cats = classifier_tfidf._find_similar_categories(product_text, top_k=5)
        
        print(f"\nTop 5 Most Similar Categories:")
        for idx, row in similar_cats.iterrows():
            print(f"  {row['similarity_score']:.3f} - {classifier_tfidf._get_full_path_names(row['path'])}")
        
        # Classify
        print(f"\nClasifying with Gemini...")
        type_id, path, full_name = classifier_tfidf.classify(
            product['name'],
            product['description'],
            product['specs'],
            top_k_categories=20
        )
        
        if type_id:
            print(f"Classified successfully Type ID: {type_id} Category: {full_name}")
            
        else:
            print("Classification failed")
```



### Test Single Website
```python
# Comment out the other scraper in run() method:
def run(self, max_products_per_site=50):
    # Only Sika
    sika_urls = self.sika_scraper.get_product_urls_from_sitemap()
    for url in sika_urls[:max_products_per_site]: # removed slice to scrape all products
        # ... process
    
    # Comment out Flex-Tools section
    # logger.info("\n--- Scraping Flex-Tools Products ---")
    # flex_urls = self.flex_scraper.get_all_product_urls()
    # ...
```

### Output Example
```
INFO:logger:Classified 'Sika AnchorFix®-2+ Tropical' as: Construction technology > Construction chemicals > Joint sealant, water stop, joint profile (building material) > Hot compound (building material)
  Confidence: high | Reasoning: The product is an anchoring adhesive, which falls under the broader category of construction chemica...
```

