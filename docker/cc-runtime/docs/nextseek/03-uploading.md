# Uploading

## Uploading Samples

### Excel Sheet Structure

Samples are tabular metadata and are uploaded to the database as Excel sheets. These Excel sheets must be structured in a specific format to be successfully uploaded.

Properly formatted Upload Sheets have four sub-sheets: **Instructions, Samples, Ontology, and Assay.**

**Instructions:** This sheet contains all of the information required to add the sample to the Database. There are four required columns in this sheet: *Field, Database Field, Field Type, and Ontology.* \
\&#xNAN;*Field* = An identical match to the headers of the Samples Page. The headers/column names do **NOT** need to be the database name of the attribute.\
\&#xNAN;*Database Field* = Formatted as SAMPLETYPE::Attribute Name -> Ex: TIS::Type or D.SEQ::Name. The Attribute name here **MUST** exactly match the exact DB Field Name. This maps the value in the Samples sheet to the correct Attribute.\
\&#xNAN;*Field Type* = Text, Number, Date, Controlled Ontology \
\&#xNAN;*Ontology* = If Field Type == Controlled Ontology, the name of the Ontology (in the Ontology) sheet.&#x20;

**Samples**: This is the table of metadata, where each row is a sample, and each column is an attribute for that sample. The column headers (Row 1) are the attribute names, and must identically match the *Field* column (transposed) of the Instructions page.

* For samples, Name and File\_Primary Data must be unique (per sample type). There cannot be two samples in the database with the same Name or File\_PrimaryData (per sample type)

**Ontology:** This sheet contains ontologies (sets of controlled vocabulary terms) that can be used to control the values of an attribute. For an ontology to be enforced, the "Field Type" on the Instructions page for that attribute must be set to "Controlled Ontology", and the name of the Ontology (header in the Ontology sheet) must be set as the "Ontology" of that attribute on the Instructions Page. See the image below for more clarification.

**Assay:** This sheet determines which Assay(s) the uploaded samples should be associated with. The required columns are: SampleType, AssayType, Assay, Direction.

<figure><img src="/files/OcviqOblz5NNc3EYpYSb" alt=""><figcaption><p>Visual Representation of sheets as explained above</p></figcaption></figure>

Attached is an example sample sheet, with notes/annotations as described above.

{% file src="/files/YlgTXLhnssSM0FpO0GpX" %}
A SampleSheet Template Upload Sheet with some extra notes, as explained above
{% endfile %}

### Assay Sheet, Sample Sheet, and Update Sheets

There are three different types of upload sheets: Assay Sheets, Sample Sheets, and Update Sheets.

* Assay Sheets / Sample Sheets follow the Excel Sheet Format as shown above.
  * Assay/Sample Sheets must be used to upload a sample for the first time, to generate the UID.&#x20;
    * The first time you upload an Assay/Sample Sheet, the UID column should be blank and will be automatically generated. Following upload, paste the UIDs into your upload sheet from the auto-generated feedback sheet.
    * If you try and update an already registered sample without its UID, the system will not allow it. It will think you are trying to upload a new sample, but then throw an error because a sample with that name already exists.
  * When using an Assay / Sample Sheet to update samples->  **All attributes** for that sample must be included. If an attribute is not included at a later update, that metadata will be removed from the sample.
* An Assay Sheet contains multiple sample types, while a Sample Sheet contains a single sample type.
* Update Sheets are used to *update* a subset of attributes for a sample that has already been uploaded. UID's must be included, and the attribute header must match the database name.

Below are examples of an Assay Sheet / Update Sheet. An Example of a Sample Sheet is linked above (SampleSheetFormatting\_Template\_240824.xlsx)

{% file src="/files/YIdFE2ptBx9CTVI7CCqf" %}
This AssaySheet will upload 12 NHP's and 12 TIS's (each of them automatically associated with those NHPs).
{% endfile %}

{% file src="/files/y2hPKB6wxTj0ql0UZDQz" %}
This update sheet will only update the two attributes listed (Sex and DateOfBirth) for those 12 NHPs
{% endfile %}

### Sample Validation Script

Once you have formatted your Assay/Sample Sheet for Upload, there exists a Sample Validation Script on the [Uploading](https://nextseek.mit.edu/seek/samples/upload/) page to check that the sheet is in the correct format.&#x20;

<figure><img src="/files/lYbQT6nGzJP6BmHlIysP" alt=""><figcaption><p>Sample Validation Check on the Upload Page</p></figcaption></figure>

Choose your prepared Assay/Sample Sheet (*not applicable for update sheets*) and click validate.

<figure><img src="/files/Zma9cpTs6s3MbhX3UBPp" alt=""><figcaption><p>Logging output function that shows what the validation check looks for</p></figcaption></figure>

The validation script checks:

* That the Excel sheet is formatted correctly (Instructions, Samples, Ontology, Assay)&#x20;
* That the Instructions Page is formatted correctly (Field, Database Field, Field Type, Ontology)
  * In the above example, the Ontology column is missing
* That the entries of Database Field match attributes in the database
  * In the above example, sample type CEL does not have the attribute Protocols (it should be Protocol)
* That the Header row in the Samples page == Field column in the Instructions page
  * Disregard the 'Field' error, but in the above example, it's finding that there exists an entry in the Instructions page for Source, that does not exist in the Samples page.
* That the Assay Sheet is formatted correctly (SampleType, AssayType, Assay, Direction)
  * In the above example, the column AssayType is missing, and there is an extra column named "1"

Not all of these errors would cause the upload to error out. Any error with the overall structure/format of the sheet would cause an upload to fail. A mislabeled attribute is not going to cause an error, but will instead upload that sample WITHOUT that attribute.&#x20;

It is **good practice** to test your sample sheet on sample validation before uploading.

### How to Upload

1. Once you have created your assay or sample sheet, head to the[ Uploading Page](https://nextseek.mit.edu/seek/samples/upload/)
2. Submit your sheet through Sample Validation.
3. Following validation success, place your sheet in the upload box. If you are an admin, select which lab/user you are uploading for. If not, leave as default and it will upload as yourself.

<figure><img src="/files/IuMg3qReFeJWmJUeiwOU" alt=""><figcaption><p>Sample Uploading Box</p></figcaption></figure>

4. Click Upload. Should take around 1 second per sample
   1. To track your upload, head to either Search Page **(INSERT LINK).** Search today's date in YYMMDD format (so 8/23/24 = 240823).
   2. Through running that search a few times, you should see the number of samples increasing, therefore tracking that your upload is running successfully.
5. Following upload, paste your generated UIDs from the feedback file back into your upload sheet
6. IMPORTANT: Quality checks
   1. Check a few samples
   2. Ensure that the correct # of samples got uploaded
   3. Ensure that all attributes for your samples are uploaded.

## Uploading Protocols / Data Files

To upload Protocols and Data Files, head to the[ Protocol/Data File Uploading Page](https://nextseek.mit.edu/seek/data/upload/).

<figure><img src="/files/sB3xkUZyhAV6ne71dtzB" alt=""><figcaption><p>Protocol / Data File Uploading page</p></figcaption></figure>

1. Select whether the file(s) you are uploading are Data Files or Protocols
2. If you are an admin, select which Lab/User you are uploading for. If you are not, leave it as the default
3. Place the files into the "File Dropzone" and click submit. Wait for data files/protocols to be uploaded
4. The resulting UID generated in the bottom table will be the UID used to reference that Data File or Protocol. Data File UIDs are SampleTypeUID\_FileName. Protocol UIDs are P.LAB-YYMMDD\_Version\_FileName

For Protocols, following the procedure above is sufficient to upload.

Data Files require a Sample with a File\_PrimaryData that == the name of the file you are trying to upload (to automatically match the Data File UID / Link\_PrimaryData to the corresponding samples). If there is not a D. Sample that matches your data file name, you can make a D.FILE sample to trick the system into uploading it- this is particularly useful when the file you are trying to associate is not a primary file, but a supplementary data file, such as a FASTQC.html. Below is a D.FILE\_Template.

{% file src="/files/oTM1uGDXfA57Q9LXxeFZ" %}

#### Documentation surrounding Globus (Uploading and Downloading) exists [here:](/mit-data-management-analysis-core/searching-downloading.md#globus)

