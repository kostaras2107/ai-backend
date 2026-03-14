import pandas as pd

df = pd.read_csv("categories.csv")

def map_category(cat):

    cat = str(cat).lower()

    # BOOKS
    if any(x in cat for x in ["βιβλ","book","novel","poetry","literature","comic"]):
        return "books"

    # FASHION
    if any(x in cat for x in ["ρούχ","dress","skirt","shirt","shorts","t-shirt","polo","hoodie","leggings","tracksuit","clothing"]):
        return "fashion"

    # SHOES
    if any(x in cat for x in ["παπού","shoe","sneaker","boot","loafer","sandals","heels"]):
        return "shoes"

    # MOBILE ACCESSORIES
    if any(x in cat for x in ["θήκ","κινητ","phone","iphone","charger","airpod","screen protector","tempered"]):
        return "mobile_accessories"

    # SMARTPHONES
    if any(x in cat for x in ["smartphone","κινητή τηλεφωνία","mobile phone"]):
        return "smartphones"

    # COMPUTERS
    if any(x in cat for x in ["comput","laptop","mac","monitor","ram","motherboard","desktop","tablet"]):
        return "computers"

    # GAMING
    if any(x in cat for x in ["gaming","playstation","xbox","nintendo","funko","lego"]):
        return "gaming"

    # BEAUTY
    if any(x in cat for x in ["μακιγ","beauty","cosmetic","spf","cream","lipstick","makeup","skincare"]):
        return "beauty"

    # JEWELRY
    if any(x in cat for x in ["κοσμ","κολιέ","σκουλαρ","δαχτυλ","bracelet","necklace","ring","earring","watch","ρολό"]):
        return "jewelry"

    # HOME
    if any(x in cat for x in ["σπιτ","home","furniture","bed","sofa","table","λευκά είδη","salon","decor"]):
        return "home"

    # LIGHTING
    if any(x in cat for x in ["φωτισ","lamp","led","lighting"]):
        return "lighting"

    # TOOLS
    if any(x in cat for x in ["εργαλ","tool","drill","screw"]):
        return "tools"

    # TOYS
    if any(x in cat for x in ["παιχν","toy","playmobil","baby","kids"]):
        return "toys"

    # SPORTS
    if any(x in cat for x in ["sport","fitness","gym","running"]):
        return "sports"

    return "other"


df["master_category"] = df["root_category"].apply(map_category)

df.to_csv("categories_mapped.csv", index=False)

print("DONE")