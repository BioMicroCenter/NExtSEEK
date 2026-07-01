# Searching / Downloading

## Searching

### [Simple Search Page](https://nextseek.mit.edu/seek/search/?tab=simple)

This page allows you to search a single sample type, by a single attribute:value. In the example below, I am searching for all NHP where the Species attribute contains the value 'Macaca'.&#x20;

<figure><img src="/files/K3MZK2ENaDLjuwEDDhSy" alt=""><figcaption><p>Simple Search Page</p></figcaption></figure>

The resulting table returns all samples that match the query. It displays the Assays, Contributor, and Attribute:Value that it found. All of the empty boxes underneath the headers are text filterable. To view the sample page of a specific sample, click on its hyperlinked UID.

### [Advanced Search Page](https://nextseek.mit.edu/seek/search/?tab=advanced)

The advanced search page allows for complex querying (AND/OR/NOT) across the entire database. Additionally, you can select if you want it to be partial/exact matches, or limit it to a specific sample type.

{% embed url="<https://vimeo.com/1002111455>" %}
Advanced Search Demo Video
{% endembed %}

The resulting table is identical to the Simple Search page, with one additional feature: Send to Sample Retrieval. Following a search, you can select a subset of samples, and send them to [sample retrieval. ](#sample-retrieval-download)

### Search by UID

On the top bar of every page, there exists a Search by UID box.

<figure><img src="/files/jCPzDJWR5RR8s8zjPKyR" alt=""><figcaption></figcaption></figure>

By entering a valid UID and pressing search, you automatically redirect to that sample page (assuming you have access and it is a valid UID). Remember, sample pages with lots of associated samples take longer to load.

### Searching on the SEEK Website

On the SEEK website, you can either search all assets in the top search bar (Search here...) or head to Browse and select a specific asset type that you would like to search.

<figure><img src="/files/1OKQNZqR8YpdLz3Li73I" alt=""><figcaption><p>SEEK Website Search</p></figcaption></figure>

## Downloading

### Downloading via Search Pages

The first step of downloading via Search pages is to search for the samples you want to download.&#x20;

When downloading samples, you must choose whether you want to download just the samples you are looking up or include all samples associated (parent/children).

{% embed url="<https://vimeo.com/1002832736>" %}
Download with Parents
{% endembed %}

In the pop-up window, it asks if you would like to download with Parents or not. By selecting NO, you will download the samples that have been selected. By selecting yes, you will download all associated samples (parent and child).

This is only an option on the Simple Search page. on the Advanced search page, it automatically will include parents. Be patient when downloading a large subset of samples and their parents.

Attached below is an example of a downloaded file from NExtSEEK - with parents. This data is published already and associated with: <https://fairdomhub.org/studies/1134>.

{% file src="/files/Ezoj6FBaEWMoCn28C72H" %}
Example download file
{% endfile %}

### [Sample Retrieval](https://nextseek.mit.edu/seek/search/?tab=new-retrieve)

Sample Retrieval is a feature that allows a user to download all of the associated samples (parents and children) of the sample(s) searched.&#x20;

How to Use Sample Retrieval:

1. Input samples into Sample Retrieval: by pasting in UIDs (delimited by newlines), or sending samples over from advanced search
2. Run sample retrieval
3. View downloaded file that contains all associated samples

Usually - sample retrieval is used after identifying what samples you are interested in, via a different search method. Once you have a set of UIDs, head to Sample Retrieval, to pull all associated data from those associated samples.

### [Protocol](https://nextseek.mit.edu/seek/sop/query/) / [Data File](https://nextseek.mit.edu/seek/datafile/query/) Query and Download

These two pages are identical (images below). They are filterable tables that allow you to search what protocol/data files are visible to you, along with links to download individual files, and an option to download files in batch.&#x20;

<figure><img src="/files/7gjqsyDOa4JvyDbUoI7M" alt=""><figcaption><p>Filtering the SOP query page by the UID contains FLY</p></figcaption></figure>

<figure><img src="/files/tqPyreTdpTE4RKepfLWb" alt=""><figcaption><p>Filtering the Data File query page by the UID contains SAS</p></figcaption></figure>

To download a specific file, click the File URL. To batch download files, select the checkbox, and select Batch download files selected. The original file name redirects you to the SEEK page for that specific data file/protocol.

### Globus

[Globus](https://www.globus.org/) is a cloud-based file transfer and storage service that allows users to move and share large amounts of data between different resources.

NExtSEEK houses metadata that describes/annotates actual data files. NExtSEEK does not have a good solution for storing and sharing data files, while Globus does. Below is an overview of how Globus and NExtSEEK are used together.

1. Register for a [Globus](https://app.globus.org/) account with your Institution email
2. Email <fairdata@mit.edu> with your Globus Username and Project Association
3. The fairdata team will reply once you have been added to the correct Globus Collections
4. Head to [Collections > Shared with You](https://app.globus.org/collections?scope=shared-with-me) to see the collections shared with you
5. There exist two collections that you will have access to: {Project\_Name}-Staging and {Project\_Name}-Public

Data is Uploaded to {Project\_Name}-Staging and Downloaded from {Project\_Name}-Public

The fairdata team will curate (move) data from Staging to Public when the relevant metadata has been uploaded to NExtSEEK. Following "curation", the Link\_PrimaryData attribute of the D.SampleType on NExtSEEK, will be the corresponding Globus link.

The full Globus documentation exists here: <https://docs.globus.org/>

## Deleting

### Deleting Samples

Deleting samples happens on the search pages. Similar to downloading, first, you need to search and select the samples you want to delete. You also need to ensure that no samples are children of the samples you are trying to delete.&#x20;

For example, in the image below, If I am trying to delete those 5 NHPs, no samples in the database can have those 5 NHPs as their parent.

<figure><img src="/files/oBXttSkGi5YWTgRz5C5C" alt=""><figcaption><p>Deleting samples image</p></figcaption></figure>

Once you've selected the samples, click delete, and assuming you are admin, type 'DELETE'. Let it run - takes around 6 seconds per sample - and again, will error out if there are downstream samples associated.

There also exists a "Sample Deletion" tab on the Search pages, that allows sample deletion by pasting in UIDs - that way you don't need to search the samples via other search methods.

### Deleting Protocols and Data Files

To delete a Protocol or Data File - head to the SEEK website:

<figure><img src="/files/EzpFVo9dMjlUG5QfgdLl" alt=""><figcaption></figcaption></figure>

Find the Protocol / Data File you want to delete, click actions, and delete.&#x20;

